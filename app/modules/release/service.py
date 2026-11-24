from app.modules.ledger.identity import canonical, sha

from .audit import authenticate


def release(manifest, tables, results, receipts=()):
    chain = authenticate(manifest, tables, results, receipts)
    excluded = {
        'intake':results['returns']['rejected'], 'alignment':results['frame']['invalid'],
        'nonresponse':results['frame']['nonresponse'],
    }
    payload = dict(manifest=manifest,originals=tables,results=results,excluded=excluded)
    proof = dict(**payload,seal=sha(canonical(payload)),verified=True)
    if chain:
        proof['legacy_seal'] = chain[0]['pre_seal']
        proof['withdrawals'] = [dict(ticket=hop['ticket'],removed=hop['removed'],
                                     created=hop['created'],pre_seal=hop['pre_seal'])
                                for hop in chain]
    return proof
