from datetime import UTC, datetime
from time import perf_counter_ns

from app.config import SPEC
from app.errors import SurveyProblem
from app.modules.returns.scrub import guarded
from app.pipeline import run

from .identity import canonical, identity, sha


def archive(service, tables):
    start = perf_counter_ns()
    for _ in range(4):
        excluded = service.withdrawn()
        tables = guarded(tables, excluded)
        results = run(tables)
        key, hashes = identity(tables)
        digest = sha(canonical(results))
        summary = dict(id=key,files=hashes,version=SPEC,created=datetime.now(UTC).isoformat(timespec='seconds'),
                       result_sha256=digest, elapsed_ms=(perf_counter_ns()-start)//1_000_000)
        try:
            fresh = service.add(summary,tables,results,assumed=excluded)
        except SurveyProblem as exc:
            if exc.code != 'WITHDRAWN_CHANGED':
                raise
            continue
        break
    else:
        raise SurveyProblem('WITHDRAWN_CHANGED','撤回名单频繁变化，请重试',409)
    manifest,_,_ = service.read(key)
    if manifest['result_sha256'] != digest:
        raise SurveyProblem('REPLAY_DIFFERENCE','同源归档摘要存在冲突',409)
    return dict(id=key,manifest=manifest,reused=not fresh,balance=results['balance'])


def replay(service, key):
    '''按留档原件复算，与当前留档结果比对；撤回剔除后的批次对剔除后留档重放。'''
    manifest, tables, results = service.read(key)
    fresh = run(guarded(tables, service.withdrawn()))
    if sha(canonical(fresh)) != sha(canonical(results)):
        raise SurveyProblem('REPLAY_DIFFERENCE','复算与原归档不一致',409)
    return dict(id=key,manifest=manifest,reused=True,balance=fresh['balance'])
