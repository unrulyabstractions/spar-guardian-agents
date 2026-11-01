"""The offers that can hold a box: the search, the rules, the ranking and the rent request.

The search is the one `vastai search offers` sends. A box is the kvm image as a VM with Docker inside, so an offer must
be x86_64 on a host that runs VMs, with direct ssh, reliability 0.98, 200 GB of disk, and a host outside the countries
the model providers do not serve. vast.ai gives an instance the offer's share of the machine's cores
(cpu_cores_effective). The RAM share is assumed to follow the cores (cpu_ram is the machine's), and box_setup.sh checks
the RAM each box really has.
"""
from __future__ import annotations

from scripts.box.fleet_layout import LIGHT_FLOOR
from scripts.box.vast_api import SEARCH_PATH, Call

IMAGE = "docker.io/vastai/kvm:ubuntu_terminal"
DISK_GB = 200
MIN_CORES, MIN_RAM_GB = LIGHT_FLOOR
"""The least a box of any task needs. A task's own floor is fleet_layout.floor_of."""
MIN_RELIABILITY = 0.98
MAX_DPH = 1.00
"""The dearest offer, in $/h with the disk, that offers and create take unless --max-dph says otherwise."""
BLOCKED_COUNTRIES = {"CN", "HK", "MO", "RU", "BY", "IR", "KP", "SY", "CU"}
"""Where OpenAI or Anthropic do not serve their API, or where the box could not reach it."""


def search_call(limit: int = 1000) -> Call:
    """The search `vastai search offers` sends, narrowed on the server to what the box needs (the RAM share and the
    country are checked here), on-demand prices with the box's disk included, cheapest first."""
    query = {"verified": {"eq": True}, "external": {"eq": False}, "rentable": {"eq": True},
             "cpu_arch": {"eq": "amd64"}, "vms_enabled": {"eq": True}, "cpu_cores_effective": {"gte": MIN_CORES},
             "cpu_ram": {"gte": MIN_RAM_GB * 1000}, "disk_space": {"gte": DISK_GB},
             "reliability": {"gte": MIN_RELIABILITY}, "direct_port_count": {"gte": 1}}
    return Call("POST", SEARCH_PATH, body={**query, "order": [["dph_total", "asc"]], "type": "on-demand",
                                           "allocated_storage": DISK_GB, "limit": limit})


def ram_share_gb(offer: dict) -> float:
    """The RAM an instance of the offer gets, assumed: the machine's RAM (cpu_ram, MB) times the offer's share of its
    cores."""
    cores = offer.get("cpu_cores") or 0
    share = (offer.get("cpu_cores_effective") or 0) / cores if cores else 0.0
    return (offer.get("cpu_ram") or 0) / 1000 * share


def country(offer: dict) -> str | None:
    """The two-letter code that ends geolocation ("Quebec, CA" -> "CA")."""
    location = offer.get("geolocation") or ""
    code = location.rsplit(",", 1)[-1].strip()
    return code if len(code) == 2 else None


def rejections(offer: dict, max_dph: float = MAX_DPH, floor: tuple[int, int] = LIGHT_FLOOR) -> list[str]:
    """Why an offer cannot hold a box of that floor (cores, GB of RAM); empty when it can."""
    min_cores, min_ram_gb = floor
    checks = [
        (offer.get("cpu_arch") == "amd64", "not x86_64"),
        (offer.get("vms_enabled") is True, "no VM support"),
        (not offer.get("is_vm_deverified"), "VM support deverified"),
        ((offer.get("cpu_cores_effective") or 0) >= min_cores, f"fewer than {min_cores} cores"),
        (ram_share_gb(offer) >= min_ram_gb, f"less than {min_ram_gb} GB of RAM in its share"),
        ((offer.get("disk_space") or 0) >= DISK_GB, f"less than {DISK_GB} GB of disk"),
        ((offer.get("reliability") or 0) >= MIN_RELIABILITY, f"reliability below {MIN_RELIABILITY}"),
        ((offer.get("direct_port_count") or 0) >= 1, "no open port for direct ssh"),
        (offer.get("rentable") is not False and not offer.get("rented"), "not rentable"),
        (country(offer) is not None, "no country"),
        (country(offer) not in BLOCKED_COUNTRIES, "a country the model providers do not serve"),
        (offer.get("dph_total") is not None, "no price"),
        ((offer.get("dph_total") or 0) <= max_dph, f"dearer than ${max_dph:.2f}/h"),
    ]
    return [reason for ok, reason in checks if not ok]


def candidates(offers: list[dict], max_dph: float = MAX_DPH) -> list[dict]:
    """The offers that can hold a box, cheapest first, then the most reliable, then by id."""
    return sorted((o for o in offers if not rejections(o, max_dph)),
                  key=lambda o: (o["dph_total"], -o["reliability"], o["id"]))


def offer_row(offer: dict) -> str:
    return (f"{offer['id']:>9} {offer.get('machine_id', ''):>7} {offer['dph_total']:7.3f} "
            f"{offer.get('cpu_cores_effective', 0):5.0f}/{offer.get('cpu_cores', 0):<4} "
            f"{ram_share_gb(offer):6.0f}/{(offer.get('cpu_ram') or 0) / 1000:<5.0f} {offer.get('disk_space', 0):6.0f} "
            f"{offer.get('reliability', 0):6.4f} {'yes' if offer.get('vms_enabled') else 'no':>3} "
            f"{offer.get('inet_down', 0):7.0f}  {offer.get('geolocation') or '-'}")


OFFER_HEADER = (f"{'offer':>9} {'machine':>7} {'$/h':>7} {'cores':>10} {'RAM GB':>12} {'disk':>6} {'reliab':>6} "
                f"{'vm':>3} {'down':>7}  location   (cores and RAM: the offer's share/the machine's; Docker runs "
                f"inside the VM)")


def create_call(offer_id: int, label: str) -> Call:
    """The rent request: the kvm image as a VM, its disk, direct ssh and the box's label."""
    return Call("PUT", f"/api/v0/asks/{offer_id}/",
                body={"image": IMAGE, "disk": DISK_GB, "runtype": "ssh_direct", "vm": True, "label": label})
