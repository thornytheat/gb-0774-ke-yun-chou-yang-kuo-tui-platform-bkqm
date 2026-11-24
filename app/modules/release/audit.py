from app.errors import SurveyProblem
from app.modules.ledger.identity import canonical, sha


def authenticate(manifest, tables, results, receipts=()):
    '''核验归档内容。原件未撤回时直接比对签发摘要；含已撤回票时沿撤回回执
    哈希链核验：归档 manifest == 首段回执撤回前摘要，末段回执剔除后摘要 == 当前留档。
    返回核验通过的回执链（无撤回为空列表）。'''
    files = {key: sha(text) for key, text in tables.items()}
    digest = sha(canonical(results))
    chain = []
    if (files, digest) != (manifest['files'], manifest['result_sha256']):
        want_files, want_result = manifest['files'], manifest['result_sha256']
        pool = list(receipts)
        while (files, digest) != (want_files, want_result):
            hop = next((r for r in pool
                        if r['pre_files'] == want_files and r['pre_result'] == want_result), None)
            if hop is None:
                raise SurveyProblem('BAD_SEAL', '原始样本摘要校验失败', 409)
            chain.append(hop)
            pool.remove(hop)
            want_files, want_result = hop['post_files'], hop['post_result']
    if not results['balance']['balanced']:
        raise SurveyProblem('UNBALANCED', '人口或回收行尚未对平', 409)
    return chain
