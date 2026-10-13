"""Deterministic world generator: 200 firms (P-0001..P-0200), 3 lenders (L1..L3), director clusters, alias pairs, and HKDF Ed25519 keys."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from contract.models import IndustryClass, Party, make_gstin
from data.generator.cities import CITIES


@dataclass(frozen=True)
class GeneratedWorld:
    seed: int
    regime: str
    parties: list[Party]
    parties_by_id: dict[str, Party]
    firms: list[Party]
    lenders: list[Party]
    alias_pairs: list[dict[str, Any]]
    director_clusters: list[dict[str, Any]]
    keys_manifest: dict[str, Any]
    private_keys_hex: dict[str, str]


PREFIX_NAMES = [
    "Sahyadri", "Deccan", "Kaveri", "Bharat", "Coromandel", "Vindhya", "Godavari", "Narmada",
    "Aravalli", "Konkan", "Malabar", "Nilgiri", "Satpura", "Chola", "Maratha", "Shivalik",
    "Indus", "Ganga", "Yamuna", "Krishna", "Tungabhadra", "Pennar", "Mahanadi", "Sabarmati",
    "navbharat", "Surya", "Pragati", "Vikram", "Ashoka", "Maurya", "Kalinga", "Pallava",
    "Kakatiya", "Hoysala", "Chalukya", "Rashtrakuta", "Vanguard", "Apex", "Beacon", "Crest",
]

SECTOR_TEMPLATES: dict[IndustryClass, list[str]] = {
    IndustryClass.MANUFACTURING: [
        "Precision Castings", "Drivetrain Assemblies", "Sintered Metals", "Flat Steel Mills",
        "Forge & Transmission", "Heavy Engineering", "Auto Components", "Alloy Foundry",
        "CNC Machining Works", "Industrial Fasteners",
    ],
    IndustryClass.TRADING: [
        "Alloys Trading", "Ferrous Traders", "Metal Mercantile", "Industrial Supplies",
        "Steel Impex", "Commodity Enterprises", "Hardware Syndicate", "Engineering Traders",
    ],
    IndustryClass.DISTRIBUTION: [
        "Coil Distributors", "Fleet Logistics & Supply", "Industrial Distribution",
        "Supply Chain Hub", "Warehouse & Freight Corp", "Regional Distributors",
    ],
    IndustryClass.SERVICES: [
        "Digital Systems", "Cloud ERP Consulting", "Engineering Analytics",
        "Industrial Automation Services", "IT Infrastructure Solutions", "Software Labs",
    ],
}

ALIAS_BASE_DEFS = [
    ("Shree Ganesh Traders Pvt Ltd", "SHREE GANESH TRADERS PRIVATE LIMITED", "same_name_and_address"),
    ("Mahalakshmi Ispat & Alloys Pvt. Ltd.", "MAHALAKSHMI ISPAT AND ALLOYS PRIVATE LIMITED", "same_name_and_address"),
    ("Vishwakarma Auto Forgings Pvt Ltd", "VISHWAKARMA AUTO FORGINGS PRIVATE LIMITED", "same_name_and_address"),
    ("Siddhi Vinayak Steel Co Pvt Ltd", "SIDDHI VINAYAK STEEL COMPANY PRIVATE LIMITED", "same_name_and_address"),
    ("Tirupati Balaji Metals Pvt. Ltd.", "TIRUPATI BALAJI METALS PRIVATE LIMITED", "same_name_and_address"),
    ("Rajputana Heavy Castings Pvt Ltd", "RAJPUTANA HEAVY CASTINGS PRIVATE LIMITED", "same_name_and_address"),
    ("Patliputra Industrial Corp Pvt Ltd", "PATLIPUTRA INDUSTRIAL CORPORATION PRIVATE LIMITED", "same_name_and_address"),
    ("Chhatrapati Engineering Works Pvt Ltd", "CHHATRAPATI ENGINEERING WORKS PRIVATE LIMITED", "same_name_and_address"),
    ("Karnavati Multi-State Steels Pvt Ltd", "KARNAVATI MULTI STATE STEELS PRIVATE LIMITED", "same_pan_multi_gstin"),
    ("Bharathi Southern Alloys Pvt Ltd", "BHARATHI SOUTHERN ALLOYS PRIVATE LIMITED", "same_pan_multi_gstin"),
    ("Doaba Precision Tools Pvt Ltd", "DOABA PRECISION TOOLS PRIVATE LIMITED", "same_pan_multi_gstin"),
    ("Triveni Poly-Metals Pvt Ltd", "TRIVENI POLY METALS PRIVATE LIMITED", "same_pan_multi_gstin"),
    ("Golkonda Infra Supplies Pvt Ltd", "GOLKONDA INFRA SUPPLIES PRIVATE LIMITED", "same_pan_multi_gstin"),
    ("Utkal Minerals & Ferrous Pvt Ltd", "UTKAL MINERALS AND FERROUS PRIVATE LIMITED", "same_pan_multi_gstin"),
    ("Gomti Industrial Traders Pvt Ltd", "GOMTI INDUSTRIAL TRADERS PRIVATE LIMITED", "same_pan_multi_gstin"),
    ("vidarbha Rolling Mills Pvt Ltd", "VIDARBHA ROLLING MILLS PRIVATE LIMITED", "same_pan_multi_gstin"),
]


def derive_party_keypair(seed: int, party_id: str) -> tuple[str, str]:
    """Derive deterministic Ed25519 (private_key_hex, public_key_hex) via HKDF."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"tessera-seed",
        info=f"{seed}:{party_id}".encode("utf-8"),
    )
    priv_bytes = hkdf.derive(f"tessera-ikm:{seed}".encode("utf-8"))
    priv_key = Ed25519PrivateKey.from_private_bytes(priv_bytes)
    pub_bytes = priv_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv_bytes.hex(), pub_bytes.hex()


def sign_with_party_key(seed: int, party_id: str, payload_bytes: bytes) -> str:
    """Sign payload_bytes deterministically with the party's HKDF-derived Ed25519 private key."""
    priv_hex, _ = derive_party_keypair(seed, party_id)
    priv_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
    return priv_key.sign(payload_bytes).hex()


def _make_pan(rng: random.Random, idx: int, letter: str = "A") -> str:
    alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    c4 = alpha[(idx + rng.randint(0, 25)) % 26]
    digits = f"{1000 + (idx * 37 + rng.randint(1, 99)) % 8999:04d}"
    c9 = alpha[(idx * 7 + rng.randint(0, 25)) % 26]
    return f"AABC{c4[:1]}{digits}{c9}"


def generate_world(seed: int, regime: str = "A", num_firms: int = 200) -> GeneratedWorld:
    """Deterministically generate ~200 firms, 3 lenders, director clusters, alias pairs, and Ed25519 keys."""
    rng = random.Random(f"tessera-world:{regime}:{seed}")

    # Pre-generate 32 family/holding director clusters so 2-6 firms share 2+ directors
    director_clusters: list[dict[str, Any]] = []
    cluster_by_firm_idx: dict[int, list[str]] = {}
    firm_cursor = 1
    for cluster_idx in range(1, 33):
        size = rng.randint(2, 5)
        dins = [f"DIN-{cluster_idx:02d}{rng.randint(1000, 9999):04d}{j:02d}" for j in range(1, 4)]
        member_ids: list[str] = []
        for _ in range(size):
            if firm_cursor <= 120:
                cluster_by_firm_idx[firm_cursor] = dins[:2]
                member_ids.append(f"P-{firm_cursor:04d}")
                firm_cursor += 1
        if len(member_ids) >= 2:
            director_clusters.append(
                {
                    "cluster_id": f"DC-{cluster_idx:02d}",
                    "directors": dins[:2],
                    "party_ids": member_ids,
                }
            )

    firms: list[Party] = []
    private_keys_hex: dict[str, str] = {}
    keys_manifest_entries: dict[str, dict[str, str]] = {}

    # Industry distribution across 200 firms:
    # 1..168 regular + cluster firms; 169..200 (32 firms = 16 alias pairs)
    alias_start_idx = num_firms - len(ALIAS_BASE_DEFS) * 2 + 1  # 169 for num_firms=200

    for idx in range(1, alias_start_idx):
        party_id = f"P-{idx:04d}"
        city_info = CITIES[(idx * 7 + rng.randint(0, 15)) % len(CITIES)]
        if idx % 10 in (1, 2, 3, 4):
            ind = IndustryClass.MANUFACTURING
        elif idx % 10 in (5, 6, 7):
            ind = IndustryClass.TRADING
        elif idx % 10 in (8, 9):
            ind = IndustryClass.DISTRIBUTION
        else:
            ind = IndustryClass.SERVICES

        prefix = PREFIX_NAMES[(idx + rng.randint(0, len(PREFIX_NAMES) - 1)) % len(PREFIX_NAMES)].title()
        tmpl_list = SECTOR_TEMPLATES[ind]
        sector_word = tmpl_list[idx % len(tmpl_list)]
        suffix = "Pvt Ltd" if idx % 3 != 0 else "Ltd"
        name = f"{prefix} {sector_word} {suffix}"

        pan = _make_pan(rng, idx)
        state_code = city_info["state_code"]
        gstin = make_gstin(state_code, pan, "1")
        dins = cluster_by_firm_idx.get(
            idx,
            [f"DIN-9{idx:04d}01", f"DIN-9{idx:04d}02"],
        )
        # Share bank account within the first 10 director clusters to create strong corporate bridges
        if idx in cluster_by_firm_idx and idx <= 35:
            bank_key = f"CLUSTER-BANK:{dins[0]}".encode("utf-8")
        else:
            bank_key = f"BANK:{seed}:{party_id}:{pan}".encode("utf-8")

        priv_hex, pub_hex = derive_party_keypair(seed, party_id)
        private_keys_hex[party_id] = priv_hex
        keys_manifest_entries[party_id] = {
            "party_id": party_id,
            "public_key_hex": pub_hex,
            "kdf": "HKDF-SHA256",
            "info": f"{seed}:{party_id}",
        }

        firms.append(
            Party(
                party_id=party_id,
                name=name,
                gstin=gstin,
                pan=pan,
                cin=f"U{27000 + idx}MH2018PTC{100000 + idx}",
                state_code=state_code,
                industry_class=ind,
                address=f"Plot {idx}, Sector {(idx % 12) + 1}, Industrial Estate, {city_info['city']}",
                pincode=city_info["pincode"],
                city=city_info["city"],
                phone=f"+91-22-{24000000 + idx:08d}",
                bank_account_hash=hashlib.sha256(bank_key).hexdigest(),
                directors=list(dins),
                public_key_hex=pub_hex,
            )
        )

    # Generate 16 explicit alias pairs (32 party records: P-0169..P-0200)
    alias_pairs: list[dict[str, Any]] = []
    for pair_offset, (name_a, name_b, alias_kind) in enumerate(ALIAS_BASE_DEFS):
        idx_a = alias_start_idx + pair_offset * 2
        idx_b = idx_a + 1
        pid_a = f"P-{idx_a:04d}"
        pid_b = f"P-{idx_b:04d}"

        city_a = CITIES[(pair_offset * 3) % len(CITIES)]
        pan_shared = _make_pan(rng, idx_a)
        shared_dins = [f"DIN-77{pair_offset:02d}001", f"DIN-77{pair_offset:02d}002"]
        shared_bank = hashlib.sha256(f"ALIAS-BANK:{seed}:{pair_offset}".encode("utf-8")).hexdigest()
        shared_address = f"Plot {10 + pair_offset}, MIDC Phase II, {city_a['city']}"
        shared_phone = f"+91-20-{28880000 + pair_offset:08d}"

        if alias_kind == "same_pan_multi_gstin":
            # Same PAN across two state GSTINs or entity codes, plus Pvt Ltd vs PRIVATE LIMITED name variation
            city_b = CITIES[(pair_offset * 3 + 10) % len(CITIES)]
            gstin_a = make_gstin(city_a["state_code"], pan_shared, "1")
            gstin_b = make_gstin(city_b["state_code"], pan_shared, "2")
            state_b = city_b["state_code"]
            pin_b = city_b["pincode"]
            cname_b = city_b["city"]
            pan_b = pan_shared
        else:
            # Same normalized name ("Pvt Ltd" vs "PRIVATE LIMITED") and exact same normalized address/pincode/phone/bank,
            # AND same PAN with entity code "2" so both name+address and PAN canonicalizers merge them!
            gstin_a = make_gstin(city_a["state_code"], pan_shared, "1")
            gstin_b = make_gstin(city_a["state_code"], pan_shared, "2")
            state_b = city_a["state_code"]
            pin_b = city_a["pincode"]
            cname_b = city_a["city"]
            pan_b = pan_shared

        priv_a, pub_a = derive_party_keypair(seed, pid_a)
        priv_b, pub_b = derive_party_keypair(seed, pid_b)
        private_keys_hex[pid_a] = priv_a
        private_keys_hex[pid_b] = priv_b
        keys_manifest_entries[pid_a] = {
            "party_id": pid_a,
            "public_key_hex": pub_a,
            "kdf": "HKDF-SHA256",
            "info": f"{seed}:{pid_a}",
        }
        keys_manifest_entries[pid_b] = {
            "party_id": pid_b,
            "public_key_hex": pub_b,
            "kdf": "HKDF-SHA256",
            "info": f"{seed}:{pid_b}",
        }

        party_a = Party(
            party_id=pid_a,
            name=name_a,
            gstin=gstin_a,
            pan=pan_shared,
            cin=f"U27100MH2017PTC{300000 + idx_a}",
            state_code=city_a["state_code"],
            industry_class=IndustryClass.TRADING,
            address=shared_address,
            pincode=city_a["pincode"],
            city=city_a["city"],
            phone=shared_phone,
            bank_account_hash=shared_bank,
            directors=shared_dins,
            public_key_hex=pub_a,
        )
        party_b = Party(
            party_id=pid_b,
            name=name_b,
            gstin=gstin_b,
            pan=pan_b,
            cin=f"U27100MH2017PTC{300000 + idx_a}",
            state_code=state_b,
            industry_class=IndustryClass.TRADING,
            address=shared_address,
            pincode=pin_b,
            city=cname_b,
            phone=shared_phone,
            bank_account_hash=shared_bank,
            directors=shared_dins,
            public_key_hex=pub_b,
        )
        firms.append(party_a)
        firms.append(party_b)
        alias_pairs.append(
            {
                "primary_party_id": pid_a,
                "alias_party_id": pid_b,
                "party_a": pid_a,
                "party_b": pid_b,
                "name_a": name_a,
                "name_b": name_b,
                "pan": pan_shared,
                "kind": alias_kind,
            }
        )

    # 3 Consortium Lenders: L1, L2, L3
    lenders: list[Party] = []
    lender_defs = [
        ("L1", "Western India Trade Finance Bank Ltd", "27", "AABCW9001L", "400001", "Mumbai Nariman"),
        ("L2", "Deccan Commercial Receivable Factors Ltd", "27", "AABCD9002L", "410501", "Pune Chakan"),
        ("L3", "Southern Peninsula Factoring Corp Ltd", "33", "AABCS9003L", "600001", "Chennai Central"),
    ]
    for lid, lname, lstate, lpan, lpin, lcity in lender_defs:
        priv_l, pub_l = derive_party_keypair(seed, lid)
        private_keys_hex[lid] = priv_l
        keys_manifest_entries[lid] = {
            "party_id": lid,
            "public_key_hex": pub_l,
            "kdf": "HKDF-SHA256",
            "info": f"{seed}:{lid}",
        }
        lenders.append(
            Party(
                party_id=lid,
                name=lname,
                gstin=make_gstin(lstate, lpan, "1"),
                pan=lpan,
                cin=f"L65191MH2005PLC{900000 + int(lid[1:])}",
                state_code=lstate,
                industry_class=IndustryClass.SERVICES,
                address=f"Tower {lid[1:]}, Financial District, {lcity}",
                pincode=lpin,
                city=lcity,
                phone=f"+91-22-6600000{lid[1:]}",
                bank_account_hash=hashlib.sha256(f"LENDER-BANK:{lid}".encode("utf-8")).hexdigest(),
                directors=[f"DIN-99000{lid[1:]}01", f"DIN-99000{lid[1:]}02"],
                public_key_hex=pub_l,
            )
        )

    all_parties = [*firms, *lenders]
    parties_by_id = {p.party_id: p for p in all_parties}
    keys_manifest = {
        "schema_version": 1,
        "regime": regime,
        "seed": seed,
        "kdf": "HKDF-SHA256",
        "salt_utf8": "tessera-seed",
        "keys": keys_manifest_entries,
    }
    return GeneratedWorld(
        seed=seed,
        regime=regime,
        parties=all_parties,
        parties_by_id=parties_by_id,
        firms=firms,
        lenders=lenders,
        alias_pairs=alias_pairs,
        director_clusters=director_clusters,
        keys_manifest=keys_manifest,
        private_keys_hex=private_keys_hex,
    )


def write_world_keys(world: GeneratedWorld, regime_dir: Path, keys_root_dir: Path) -> None:
    """Write public keys manifest to regime_dir/keys_manifest.json and private keys to gitignored keys_root_dir."""
    regime_dir.mkdir(parents=True, exist_ok=True)
    (regime_dir / "keys_manifest.json").write_text(
        json.dumps(world.keys_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    priv_dir = keys_root_dir / f"regime_{world.regime}"
    priv_dir.mkdir(parents=True, exist_ok=True)
    (priv_dir / "private_keys.json").write_text(
        json.dumps(world.private_keys_hex, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
