from datetime import UTC, datetime

from app.errors import SurveyProblem
from app.modules.allocation.service import expand
from app.modules.balance.service import review
from app.modules.frame.service import align
from app.modules.ledger.identity import canonical, sha
from app.modules.returns.scrub import scrubbed_returns
from app.modules.returns.service import collect
from app.modules.weights.service import calibration

from .service import release


def recalculated(tables):
    '''对剔除后的原件重跑扩推管线；回收原件被剔空时按零回收重算。'''
    try:
        intake = collect(tables['returns'])
    except SurveyProblem as exc:
        if exc.code != 'NO_RECORDS':
            raise
        intake = dict(totals=dict(segments=1, blanks=0, input=0, selected=0, isolated=0),
                      accepted=[], rejected=[], reasons={})
    frame = align(intake, tables['frame'])
    weights = calibration(frame)
    allocation = expand(weights)
    return dict(returns=intake, frame=frame, weights=weights, allocation=allocation,
                balance=review(intake, frame, weights, allocation))


def withdraw(store, ticket):
    '''登记一票撤回：答案从全部收存原件与历次留档永久清除，并为每个受影响归档
    留下撤回回执（撤回前签发摘要与前后哈希链）。同一票重复撤回不新增签发。'''
    ticket = (ticket or '').strip()
    if not ticket:
        raise SurveyProblem('BAD_TICKET', '请提供要撤回的票号')
    stamp = datetime.now(UTC).isoformat(timespec='seconds')

    def scrub(manifest, tables, results, receipts):
        text, removed = scrubbed_returns(tables['returns'], {ticket})
        if not removed:
            return None
        try:
            pre_seal = release(manifest, tables, results, receipts)['seal']
        except SurveyProblem as exc:
            if exc.code != 'UNBALANCED':
                raise
            pre_seal = None
        pre_files = {name: sha(csv_text) for name, csv_text in tables.items()}
        pre_result = sha(canonical(results))
        tables = {**tables, 'returns': text}
        results = recalculated(tables)
        receipt = dict(run=manifest['id'], ticket=ticket, created=stamp, removed=removed,
                       pre_files=pre_files, pre_result=pre_result, pre_seal=pre_seal,
                       post_files={name: sha(csv_text) for name, csv_text in tables.items()},
                       post_result=sha(canonical(results)))
        return tables, results, receipt

    affected = store.apply_withdrawal(ticket, stamp, scrub)
    if affected is None:
        return dict(store.withdrawal(ticket), reused=True)
    return dict(ticket=ticket, created=stamp, runs=affected, reused=False)
