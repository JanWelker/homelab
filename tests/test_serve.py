"""Unit tests for the boot server's pure helpers. Nothing here binds a port."""

import json
import logging

import pytest

from boot_server import serve

MENU = """DEFAULT {default}
PROMPT 0
TIMEOUT 50

LABEL localboot
    MENU LABEL ^Boot from local disk (default)
    LOCALBOOT 0

LABEL install
    MENU LABEL ^Install Flatcar to /dev/sda (ERASES THE DISK)
    KERNEL http://10.0.0.5:8000/flatcar_production_pxe.vmlinuz
    INITRD http://10.0.0.5:8000/flatcar_production_pxe_image.cpio.gz
    APPEND flatcar.first_boot=1 ignition.config.url=http://10.0.0.5:8000/ignition-{host}-install.json
"""

ODIN_MENU = '01-fc-3f-db-0d-8f-5c'
THOR_MENU = '01-ec-8e-b5-6e-16-e0'


def ignition(unit_contents):
    """An Ignition config with the one unit node_role reads."""
    return json.dumps(
        {'systemd': {'units': [{'name': 'bootstrap-k8s.service', 'contents': unit_contents}]}}
    )


CONFIGURED = ('BIND_IP', 'HTTP_DIR', 'TFTP_DIR', 'PXE_DIR', 'INVENTORY', 'HTTP_PORT', 'TFTP_PORT')


@pytest.fixture(autouse=True)
def pristine_module():
    """configure() writes module globals; put them back so no test sees another's."""
    saved = {name: getattr(serve, name) for name in CONFIGURED}
    yield
    for name, value in saved.items():
        setattr(serve, name, value)
    serve.hosts_by_ip.clear()
    serve.roles_by_host.clear()


@pytest.fixture
def checkout(tmp_path):
    """A checkout the way make config leaves it: odin armed, thor on its disk."""
    (tmp_path / 'ansible').mkdir()
    (tmp_path / 'ansible' / 'inventory.yaml').write_text(
        'all:\n  vars:\n    boot_server_ip: "10.0.0.5"\n', encoding='utf-8'
    )
    http = tmp_path / 'output' / 'http'
    http.mkdir(parents=True)
    (http / 'ignition-odin.json').write_text(
        ignition('ExecStart=/usr/bin/kubeadm init --control-plane-endpoint x'), encoding='utf-8'
    )
    (http / 'ignition-thor.json').write_text(
        ignition('ExecStart=/usr/bin/kubeadm join 10.0.0.1:6443'), encoding='utf-8'
    )
    pxe = tmp_path / 'output' / 'tftp' / 'pxelinux.cfg'
    pxe.mkdir(parents=True)
    (pxe / ODIN_MENU).write_text(MENU.format(default='install', host='odin'), encoding='utf-8')
    (pxe / THOR_MENU).write_text(MENU.format(default='localboot', host='thor'), encoding='utf-8')
    serve.configure(str(tmp_path), bind='127.0.0.1')
    return tmp_path


# --- the three regexes ------------------------------------------------------


@pytest.mark.parametrize('name', [ODIN_MENU, '01-FC-3F-DB-0D-8F-5C', '01-Aa-bB-00-ff-Ff-01'])
def test_menu_name_accepts_a_pxelinux_mac_menu_in_any_case(name):
    """PXELINUX asks for 01-<mac> with the firmware's casing."""
    assert serve.MENU_NAME.match(name)


@pytest.mark.parametrize(
    'name',
    [
        'default',  # the fallback menu is not a node
        '01-fc-3f-db-0d-8f',  # five octets
        '01-fc-3f-db-0d-8f-5c-00',  # seven
        '01-fc:3f:db:0d:8f:5c',  # inventory spelling, not the menu's
        '02-fc-3f-db-0d-8f-5c',  # not the Ethernet hardware type
        'fc-3f-db-0d-8f-5c',  # no prefix
    ],
)
def test_menu_name_rejects_anything_that_is_not_one(name):
    """A near miss must not be reported as a node with an unknown MAC."""
    assert not serve.MENU_NAME.match(name)


@pytest.mark.parametrize(
    'name, host',
    [
        ('ignition-odin.json', 'odin'),
        ('ignition-odin-install.json', 'odin'),
        ('ignition-node-1-install.json', 'node-1'),
    ],
)
def test_ignition_name_yields_the_host_without_the_install_suffix(name, host):
    """Both configs name the host; the suffix is not part of it."""
    assert serve.IGNITION_NAME.match(name).group(1) == host


@pytest.mark.parametrize(
    'name',
    [
        'ignition-.json',
        'ignition-odin.yaml',
        'ignition-odin.json.bak',
        'Ignition-odin.json',
        'xignition-odin.json',
    ],
)
def test_ignition_name_rejects_other_files(name):
    """Only an exact ignition-<host>[-install].json ties a request to a host."""
    assert not serve.IGNITION_NAME.match(name)


def test_menu_host_reads_the_host_out_of_the_ignition_url():
    """The APPEND line names the installer config; the host is inside it."""
    match = serve.MENU_HOST.search(MENU.format(default='install', host='odin'))
    assert match.group(1) == 'odin'


def test_menu_host_stops_at_slashes_and_whitespace():
    """The host cannot run backwards into the URL path or forwards past the name."""
    assert serve.MENU_HOST.search('ignition-odin.json').group(1) == 'odin'
    assert serve.MENU_HOST.search('x/ignition-thor-install.json y').group(1) == 'thor'
    assert serve.MENU_HOST.search('ignition-a b.json') is None


# --- configure and the inventory -------------------------------------------


def test_configure_derives_every_path_from_root(checkout):
    """One --root moves the document roots and the inventory together."""
    assert serve.HTTP_DIR == str(checkout / 'output' / 'http')
    assert serve.TFTP_DIR == str(checkout / 'output' / 'tftp')
    assert serve.PXE_DIR == str(checkout / 'output' / 'tftp' / 'pxelinux.cfg')
    assert serve.INVENTORY == str(checkout / 'ansible' / 'inventory.yaml')
    assert serve.BIND_IP == '127.0.0.1'


def test_configure_defaults_the_bind_address_to_the_inventory(checkout):
    """Without --bind the server answers where make config pointed the menus."""
    serve.configure(str(checkout), http_port=18000, tftp_port=1069)
    assert serve.BIND_IP == '10.0.0.5'
    assert (serve.HTTP_PORT, serve.TFTP_PORT) == (18000, 1069)


def test_configure_exits_when_the_inventory_is_missing(tmp_path):
    """A wrong working directory is an exit with the path, not a traceback."""
    with pytest.raises(SystemExit, match=r'inventory\.yaml not found'):
        serve.configure(str(tmp_path))


def test_configure_exits_when_boot_server_ip_is_unset(tmp_path):
    """An inventory without the address names the variable to set."""
    (tmp_path / 'ansible').mkdir()
    (tmp_path / 'ansible' / 'inventory.yaml').write_text('all:\n  vars: {}\n', encoding='utf-8')
    with pytest.raises(SystemExit, match='boot_server_ip is not set'):
        serve.configure(str(tmp_path))


def test_parse_args_defaults_and_overrides(monkeypatch, tmp_path):
    """The flags default to the working directory and the well-known ports."""
    monkeypatch.chdir(tmp_path)
    args = serve.parse_args([])
    assert (args.bind, args.http_port, args.tftp_port) == (None, 8000, 69)
    assert args.root == str(tmp_path)
    args = serve.parse_args(
        [
            '--bind',
            '127.0.0.1',
            '--http-port',
            '18000',
            '--tftp-port',
            '1069',
            '--root',
            '/elsewhere',
        ]
    )
    assert (args.bind, args.http_port, args.tftp_port) == ('127.0.0.1', 18000, 1069)
    assert args.root == '/elsewhere'


# --- who is asking ---------------------------------------------------------


@pytest.mark.parametrize(
    'host, role', [('odin', 'control-plane'), ('thor', 'worker'), ('loki', None)]
)
def test_node_role_reads_the_bootstrap_unit(checkout, host, role):
    """kubeadm init marks the control plane; no config at all is unknown."""
    assert serve.node_role(host) == role
    assert serve.roles_by_host[host] == role


def test_node_mac_is_the_menu_name_in_inventory_spelling(checkout):
    """01-fc-3f-... on disk is fc:3f:... in inventory.yaml."""
    assert serve.node_mac('odin') == 'fc:3f:db:0d:8f:5c'
    assert serve.node_mac('loki') is None


def test_identity_pads_the_columns_for_a_known_node(checkout):
    """Role, name, address and MAC line up under one another."""
    serve.hosts_by_ip['10.0.0.11'] = 'odin'
    assert serve.identity('10.0.0.11') == (
        'control-plane odin     10.0.0.11       fc:3f:db:0d:8f:5c'
    )


def test_identity_dashes_out_what_it_does_not_know(checkout):
    """An address that never asked for an Ignition config is just an address."""
    assert serve.identity('10.0.0.99') == '-             -        10.0.0.99       -'


# --- sizes, menus and arming -----------------------------------------------


@pytest.mark.parametrize(
    'size, expected',
    [
        (0, ' (0 B)'),
        (1023, ' (1023 B)'),
        (1024, ' (1.0 KB)'),
        (3 * 1024 * 1024, ' (3.0 MB)'),
        (5 * 1024**3, ' (5.0 GB)'),
    ],
)
def test_human_size_picks_the_unit(tmp_path, size, expected):
    """Bytes are whole, everything above gets one decimal, GB is the ceiling."""
    path = tmp_path / 'file'
    with open(path, 'wb') as handle:
        handle.truncate(size)
    assert serve.human_size(str(path)) == expected


def test_human_size_is_empty_for_a_missing_file(tmp_path):
    """A 404 is narrated elsewhere; the size must not add a second complaint."""
    assert serve.human_size(str(tmp_path / 'missing')) == ''


def test_pxe_default_reads_the_default_line(checkout):
    """The DEFAULT line is the whole difference between armed and safe."""
    pxe = checkout / 'output' / 'tftp' / 'pxelinux.cfg'
    assert serve.pxe_default(str(pxe / ODIN_MENU)) == 'install'
    assert serve.pxe_default(str(pxe / THOR_MENU)) == 'localboot'
    assert serve.pxe_default(str(pxe / 'missing')) is None


def test_pxe_menus_maps_menu_files_to_hosts(checkout):
    """Only 01-<mac> files that name a host count; the rest is ignored."""
    pxe = checkout / 'output' / 'tftp' / 'pxelinux.cfg'
    (pxe / 'default').write_text('DEFAULT localboot\n', encoding='utf-8')
    (pxe / '01-00-00-00-00-00-00').write_text('DEFAULT localboot\n', encoding='utf-8')
    assert serve.pxe_menus() == {
        ODIN_MENU: ('odin', str(pxe / ODIN_MENU)),
        THOR_MENU: ('thor', str(pxe / THOR_MENU)),
    }


def test_pxe_menus_is_empty_without_a_menu_directory(tmp_path):
    """Before make config there is nothing to serve, and that is not an error."""
    serve.configure(str(tmp_path), bind='127.0.0.1')
    assert not serve.pxe_menus()


def test_switch_to_local_boot_rewrites_only_the_default_line(checkout):
    """Disarming keeps the install label so make reinstall can arm it again."""
    pxe = checkout / 'output' / 'tftp' / 'pxelinux.cfg'
    assert serve.switch_to_local_boot('odin') is True
    assert (pxe / ODIN_MENU).read_text(encoding='utf-8') == MENU.format(
        default='localboot', host='odin'
    )
    assert serve.armed_hosts() == []


def test_switch_to_local_boot_leaves_a_safe_or_unknown_menu_alone(checkout):
    """False means nothing was armed, whether the host is on disk or not."""
    pxe = checkout / 'output' / 'tftp' / 'pxelinux.cfg'
    before = (pxe / THOR_MENU).read_text(encoding='utf-8')
    assert serve.switch_to_local_boot('thor') is False
    assert serve.switch_to_local_boot('loki') is False
    assert (pxe / THOR_MENU).read_text(encoding='utf-8') == before
    assert serve.armed_hosts() == ['odin']


# --- narration -------------------------------------------------------------


def record(name, message, level=logging.ERROR):
    """A log record as tftpy or the server would emit it."""
    return logging.LogRecord(name, level, __file__, 0, message, (), None)


@pytest.mark.parametrize(
    'message',
    [
        'TFTP error: errorcode 8: ...',
        'Timeout waiting for traffic, errorcode=8',
        'File not found: /srv/tftp/pxelinux.cfg/01-fc-3f-db-0d-8f-5c',
        r'File not found: C:\tftp\pxelinux.cfg\default',
    ],
)
def test_noise_filter_drops_what_every_pxe_boot_causes(message):
    """The options handshake and the menu search list are not failures."""
    assert serve.DropKnownTftpNoise().filter(record('tftpy.TftpServer', message)) is False


@pytest.mark.parametrize(
    'name, message',
    [
        ('tftpy.TftpServer', 'File not found: /srv/tftp/lpxelinux.0'),
        ('tftpy.TftpServer', 'TFTP error: errorcode 1: File not found'),
        ('bootserver', 'File not found: /srv/tftp/pxelinux.cfg/default'),
    ],
)
def test_noise_filter_keeps_real_errors_and_other_loggers(name, message):
    """A missing bootloader is a real failure; the filter only reads tftpy."""
    assert serve.DropKnownTftpNoise().filter(record(name, message)) is True


@pytest.mark.parametrize(
    'name, expected',
    [
        ('flatcar_production_image.bin.bz2.sig', 'the OS image signature'),
        ('flatcar_production_image.bin.bz2', 'the OS image -- this is the long one'),
        ('ignition-odin.json', 'its Ignition config'),
        ('ignition-odin-install.json', 'its installer config'),
        ('flatcar_production_pxe.vmlinuz', 'the kernel'),
        ('flatcar_production_pxe_image.cpio.gz', 'the initrd'),
        ('kubernetes-v1.34.1-x86-64.raw', 'the kubernetes sysext'),
        ('robots.txt', 'robots.txt'),
    ],
)
def test_describe_names_the_file_in_the_words_the_docs_use(tmp_path, name, expected):
    """Each artifact gets its phrase; anything else is just its name."""
    assert serve.describe(name, str(tmp_path / name)) == expected


def test_describe_adds_the_size_when_the_file_is_there(tmp_path):
    """The long downloads say how long: the size goes into the phrase."""
    path = tmp_path / 'flatcar_production_image.bin.bz2'
    with open(path, 'wb') as handle:
        handle.truncate(2 * 1024 * 1024)
    assert serve.describe(path.name, str(path)) == ('the OS image (2.0 MB) -- this is the long one')


def test_announce_tftp_ties_the_client_to_its_menu(checkout, caplog):
    """A menu request is where the server first learns which node an IP is."""
    caplog.set_level(logging.INFO, logger='bootserver')
    pxe = checkout / 'output' / 'tftp' / 'pxelinux.cfg'
    serve.announce_tftp('10.0.0.11', str(pxe / ODIN_MENU))
    serve.announce_tftp('10.0.0.12', str(pxe / THOR_MENU))
    assert serve.hosts_by_ip == {'10.0.0.11': 'odin', '10.0.0.12': 'thor'}
    assert [r.getMessage() for r in caplog.records] == [
        'collecting its boot menu -- armed, so it will install',
        'collecting its boot menu -- booting from its local disk',
    ]


def test_announce_tftp_warns_about_an_unknown_mac_and_names_the_bootloader(checkout, caplog):
    """An unmatched 01-<mac> is the inventory's fault; other misses stay quiet."""
    caplog.set_level(logging.INFO, logger='bootserver')
    tftp = checkout / 'output' / 'tftp'
    serve.announce_tftp('10.0.0.13', str(tftp / 'pxelinux.cfg' / '01-00-00-00-00-00-00'))
    serve.announce_tftp('10.0.0.13', str(tftp / 'pxelinux.cfg' / 'default'))
    serve.announce_tftp('10.0.0.13', str(tftp / 'lpxelinux.0'))
    assert '10.0.0.13' not in serve.hosts_by_ip
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (
            logging.WARNING,
            'asked for 01-00-00-00-00-00-00 -- no generated menu '
            'has that MAC, check mac_address in inventory.yaml',
        ),
        (logging.INFO, 'collecting the bootloader (lpxelinux.0)'),
    ]
