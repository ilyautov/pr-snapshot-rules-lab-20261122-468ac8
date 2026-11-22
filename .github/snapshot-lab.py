import hashlib
import json
import os
import re
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO = os.environ['GITHUB_REPOSITORY']
if not re.fullmatch(r'ilyautov/pr-snapshot-rules-lab-20261122-[a-z0-9]+', REPO):
    raise SystemExit('wrong repository')
if os.environ['GITHUB_REF'] != 'refs/heads/main':
    raise SystemExit('wrong workflow ref')

SHA = re.compile(r'[a-f0-9]{40}')
DIGEST = re.compile(r'[a-f0-9]{64}')
CONTEXT = 'cordon-lab/exact-snapshot'

def api(path, method='GET', data=None, accept='application/vnd.github+json'):
    headers = {'Authorization': 'Bearer ' + os.environ['GH_APP_TOKEN'],
               'Accept': accept, 'X-GitHub-Api-Version': '2022-11-28',
               'Content-Type': 'application/json', 'User-Agent': 'disposable-pr-snapshot-lab'}
    payload = json.dumps(data).encode() if data is not None else None
    request = Request('https://api.github.com/repos/' + REPO + '/' + path,
                      data=payload, method=method, headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, response.read(1024 * 1024)
    except HTTPError as error:
        return error.code, error.read(32 * 1024)

def read(path):
    status, body = api(path)
    if status != 200:
        raise SystemExit(f'GitHub read failed: {path}, {status}')
    return json.loads(body)

def attest(case):
    number, head, base, commit = (case[k] for k in ('number', 'head', 'base', 'merge'))
    if case['base_ref'] != 'target':
        raise SystemExit('attestation is limited to the approved target')
    pull = read(f'pulls/{number}')
    observed_base = read('git/ref/heads/target')['object']['sha']
    observed_head = read(f'git/ref/pull/{number}/head')['object']['sha']
    observed_merge = read(f'git/ref/pull/{number}/merge')['object']['sha']
    parents = [parent['sha'] for parent in read(f'git/commits/{commit}')['parents']]
    diff_code, diff = api(f'compare/{base}...{head}', accept='application/vnd.github.diff')
    if not (pull['state'] == 'open' and pull['base']['ref'] == 'target'
            and head == pull['head']['sha'] == observed_head and observed_base == base
            and observed_merge == commit and parents == [base, head]
            and diff_code == 200 and hashlib.sha256(diff).hexdigest() == case['diff']):
        raise SystemExit('attested snapshot drifted')
    code, body = api(f'statuses/{commit}', 'POST', {'state': 'success', 'context': CONTEXT,
        'description': 'Verified synthetic snapshot of head and base'})
    parsed = json.loads(body)
    if not (code == 201 and parsed['state'] == 'success' and parsed['context'] == CONTEXT):
        raise SystemExit(f'App attestation failed: {code}')
    row = {'action': 'attest', 'case': case['name'], 'pr': number, 'sha': commit,
           'creator': parsed['creator']['login'], 'http': code}
    print('LAB_RESULT ' + json.dumps(row), flush=True)

def request(case):
    # The test deliberately sends the reviewed head after a separate process
    # mutates the fixture. There is no fresh client-side denial in this job.
    code, body = api(f"pulls/{case['number']}/merge", 'PUT',
                     {'sha': case['head'], 'merge_method': 'merge'})
    parsed = json.loads(body)
    row = {'action': 'request', 'case': case['name'], 'pr': case['number'],
           'expected_head': case['head'], 'http': code, 'merged': parsed.get('merged', False),
           'result_sha': parsed.get('sha'), 'message': parsed.get('message')}
    print('LAB_RESULT ' + json.dumps(row), flush=True)

cases = json.loads(os.environ['LAB_CASES'])
assert isinstance(cases, list) and 1 <= len(cases) <= 4
mode = sys.argv[1]
assert mode in ('attest', 'request')
for case in cases:
    assert type(case['number']) is int and 1 <= case['number'] <= 20
    assert isinstance(case['name'], str) and case['name'] in ('head', 'retarget', 'base', 'positive')
    assert all(SHA.fullmatch(case[key]) for key in ('head', 'base', 'merge'))
    assert DIGEST.fullmatch(case['diff'])
    (attest if mode == 'attest' else request)(case)
