from flask import Blueprint,current_app,jsonify,request

from app.errors import SurveyProblem
from app.modules.ledger.service import archive, replay
from app.modules.release.service import release
from app.modules.release.withdraw import withdraw
from app.modules.returns.scrub import guarded
from app.pipeline import STAGES,run
from app.source import sources

survey = Blueprint('survey',__name__,url_prefix='/api')


def storage():
    return current_app.extensions['survey_archive']


@survey.route('/survey/<stage>',methods=['GET','POST'])
def inspect(stage):
    if stage not in STAGES:
        raise SurveyProblem('NO_STAGE','找不到该统计阶段',404)
    if request.method=='GET' and 'run' in request.args:
        result=storage().read(request.args['run'])[2]
    else:
        body=request.get_json(silent=True) if request.method=='POST' else {'sample':request.args.get('sample','dirty')}
        result=run(guarded(sources(body),storage().withdrawn()),stage)
    return jsonify(result[stage])


@survey.get('/runs')
def list_runs():
    return jsonify(runs=storage().recent())


@survey.post('/runs')
def new_run():
    out=archive(storage(),sources(request.get_json(silent=True)))
    return jsonify(out), 200 if out['reused'] else 201


@survey.get('/runs/<key>')
def get_run(key):
    manifest,_,results=storage().read(key)
    return jsonify(id=key,manifest=manifest,balance=results['balance'],allocation=results['allocation'])


@survey.post('/runs/<key>/rerun')
def rerun(key):
    return jsonify(replay(storage(),key))


@survey.get('/export/<key>')
def export(key):
    response=jsonify(release(*storage().read(key),receipts=storage().receipts(key)))
    response.headers['Content-Disposition']=f'attachment; filename="survey-{key[:12]}.json"'
    return response


@survey.get('/withdrawals')
def list_withdrawals():
    return jsonify(tickets=sorted(storage().withdrawn()))


@survey.post('/withdrawals')
def new_withdrawal():
    body=request.get_json(silent=True)
    ticket=body.get('ticket') if isinstance(body,dict) else None
    out=withdraw(storage(),ticket)
    return jsonify(out), 200 if out['reused'] else 201
