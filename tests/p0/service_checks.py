"""Opt-in P1 service traffic checks using only disposable loopback fixtures."""

import argparse
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from egress_anchor.service_catalog import default_services
from egress_anchor.service_compiler import compile_service_subscription
from kernel_session import KernelSession
from loopback_fixtures import fixture, free_port, ping, tunnel, udp_probe


class ServiceKernelSession(KernelSession):
    def configuration(self, source, settings):
        result = compile_service_subscription(yaml.safe_dump(source), settings['services'], [settings['landing']])
        config = yaml.safe_load(result.compiled_yaml)
        config.update({
            'mixed-port': self.mixed_port, 'bind-address': '127.0.0.1',
            'external-controller': f'127.0.0.1:{self.controller_port}',
            'secret': self.secret, 'mode': 'rule', 'log-level': 'warning',
            'allow-lan': False, 'ipv6': False, 'tun': {'enable': False}, 'dns': {'enable': False},
        })
        # Keep automatic health checks inside the fixture, never on the Internet.
        for group in config['proxy-groups']:
            if group['type'] == 'url-test':
                group['url'] = 'http://127.0.0.1:1/service-health'
        path = self.directory / 'services.yaml'
        path.write_text(yaml.safe_dump(config, allow_unicode=True))
        result = subprocess.run([str(self.binary), '-t', '-d', str(self.directory), '-f', str(path)],
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise AssertionError('Mihomo rejected service config: ' + result.stdout[-1500:] + result.stderr[-1500:])
        return path


def run(binary, checks):
    events = []
    with ExitStack() as stack:
        ports = {name: stack.enter_context(fixture(kind, name, events)) for name, kind in (
            ('origin', 'echo'), ('udp', 'udp'), ('front', 'http'), ('airport', 'http'), ('landing', 'http'))}
        source = {
            'hosts': {f'{host}.p0.invalid': '127.0.0.1' for host in
                      ('service', 'docs.service', 'airport', 'direct', 'inherit', 'unrelated', 'udp')},
            'proxies': [dict(name=name, type='http', server='127.0.0.1', port=ports[name]) for name in ('front', 'airport')],
            'proxy-groups': [dict(name='AI', type='select', proxies=['front']),
                             dict(name='Other', type='select', proxies=['airport'])],
            'rules': ['DOMAIN,udp.p0.invalid,DIRECT', 'MATCH,Other'],
        }
        services = default_services()
        services[0].update(exit={'kind': 'isp', 'reference': 'home'}, extra_domains=['service.p0.invalid'],
                           exclude_domains=['docs.service.p0.invalid'])
        services[1].update(exit={'kind': 'airport', 'reference': 'AI'}, extra_domains=['airport.p0.invalid'])
        services[2].update(exit={'kind': 'direct'}, extra_domains=['direct.p0.invalid'])
        services.append(dict(id='inherited', name='Inherited', extra_domains=['inherit.p0.invalid'], exit={'kind': 'inherit'}))
        settings = {'services': services, 'landing': dict(id='home', type='http', server='127.0.0.1', port=ports['landing'])}
        core = ServiceKernelSession(binary)
        stack.callback(core.close)
        version = core.start(source, settings)
        checks.append('generated service rules including nested AND/NOT accepted')

        def visit(host):
            start = len(events)
            with tunnel(core.mixed_port, f'{host}.p0.invalid', ports['origin']) as connection:
                ping(connection)
            return events[start:]

        observed = visit('service')
        assert any(e['proxy'] in ('front', 'airport') and e['port'] == ports['landing'] for e in observed)
        assert any(e['proxy'] == 'landing' and e['port'] == ports['origin'] for e in observed)
        checks.append('ISP TCP traverses upstream and required landing')
        observed = visit('docs.service')
        assert any(e['proxy'] == 'airport' and e['port'] == ports['origin'] for e in observed)
        assert not any(e['proxy'] == 'landing' for e in observed)
        checks.append('excluded domain resumes original proxy route rather than DIRECT')
        observed = visit('airport')
        assert any(e['proxy'] == 'front' and e['port'] == ports['origin'] for e in observed)
        assert not any(e['proxy'] == 'landing' for e in observed)
        checks.append('airport route uses the selected source group without landing')
        assert not visit('direct')
        checks.append('direct service reaches origin without proxy')
        for host in ('inherit', 'unrelated'):
            assert any(e['proxy'] == 'airport' and e['port'] == ports['origin'] for e in visit(host))
        checks.append('inherited and unrelated traffic retain original route')
        assert udp_probe(core.mixed_port, 'udp.p0.invalid', ports['udp'])
        assert not udp_probe(core.mixed_port, 'service.p0.invalid', ports['udp'])
        assert udp_probe(core.mixed_port, 'udp.p0.invalid', ports['udp'])
        checks.append('generated ISP UDP guard rejects service while unrelated UDP succeeds')
        unavailable = deepcopy(settings)
        unavailable['landing']['port'] = free_port()
        core.reload(source, unavailable)
        failed = False
        try:
            visit('service')
        except OSError:
            failed = True
        assert failed, 'unavailable required landing was bypassed'
        assert any(e['proxy'] == 'airport' for e in visit('unrelated'))
        checks.append('landing failure blocks service without disturbing unrelated traffic')
        return version


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    checks = []
    report = {'time': datetime.now(timezone.utc).isoformat(),
              'scope': 'P1 compiler; isolated pinned Mihomo, loopback only, no TUN or desktop-client acceptance'}
    try:
        report['version'] = run(args.binary, checks)
        report['ok'] = True
    except Exception as error:
        report.update(ok=False, error=f'{type(error).__name__}: {error}')
    report.update(passed=len(checks), checks=checks)
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report['ok'] else 1)
