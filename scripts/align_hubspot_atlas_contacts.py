"""Align imported Atlas CRM cards with real HubSpot contact exports.

This keeps the client/company cards intact, removes generated sample support
players from imported HubSpot accounts, and attaches contacts that match by
HubSpot deal title or company name.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from psycopg.types.json import Jsonb  # noqa: E402

from backend.azureUtils.storage import client as azure_client  # noqa: E402


CRM_RECORDS_PATH = ROOT / "backend" / "data" / "crm_records.json"
DEFAULT_CONTACTS_CSV = Path(
    r"C:\Users\darri\Desktop\DevReady\imports devready\all contacts.csv"
)

GENERATED_SUPPORT_NAMES = {
    "maya chen",
    "jordan blake",
    "priya shah",
    "evan brooks",
    "nina alvarez",
    "caleb wright",
    "morgan lee",
}


def compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def clean(value: Any) -> str:
    return str(value or "").strip()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def contact_name(row: dict[str, str]) -> str:
    return " ".join(part for part in [clean(row.get("First Name")), clean(row.get("Last Name"))] if part)


def is_imported_hubspot_record(record: dict[str, Any], domain: str) -> bool:
    if record.get("domain") != domain:
        return False
    return (
        re.search("hubspot", clean(record.get("sourceSystem")), re.I)
        or clean(record.get("sourceImportBatch")).lower().startswith("hubspot-")
    )


def is_generated_support_player(player: dict[str, Any]) -> bool:
    name = clean(player.get("name")).lower()
    email = clean(player.get("email"))
    description = clean(player.get("description"))
    generated_description = re.search(
        r"DevReady engagement|vendor onboarding|day-to-day delivery expectations|technical fit|invoice routing|placed resources|first-week coordination",
        description,
        re.I,
    )
    return name in GENERATED_SUPPORT_NAMES and (email.endswith(".example") or bool(generated_description))


def relationship_role(row: dict[str, str], primary_email: str) -> str:
    email = clean(row.get("Email")).lower()
    if primary_email and email == primary_email.lower():
        return "Primary Contact"
    status = clean(row.get("Lead Status"))
    if status:
        return status
    return "Contact"


def row_to_member(row: dict[str, str], record: dict[str, Any]) -> dict[str, Any] | None:
    name = contact_name(row)
    email = clean(row.get("Email"))
    if not name and not email:
        return None
    record_id = clean(row.get("Record ID"))
    phone = clean(row.get("Mobile Phone Number")) or clean(row.get("Phone Number"))
    last_activity = clean(row.get("Last Activity Date"))
    owner = clean(row.get("Contact owner"))
    primary_email = clean(record.get("email"))
    return {
        "id": f"{record.get('id', 'CRM')}-CONTACT-{record_id or compact(email or name) or 'hubspot'}",
        "hubspotContactId": record_id,
        "name": name or email,
        "relationshipRole": relationship_role(row, primary_email),
        "email": email,
        "phone": phone,
        "title": "",
        "jobTitle": "",
        "description": "Imported from HubSpot contact export.",
        "lastConversation": last_activity or clean(record.get("lastTouched") or record.get("when")),
        "linkedinUrl": "",
        "photoUrl": "",
        "owner": owner,
        "sourceDeal": clean(row.get("Associated Deal")),
        "sourceCompany": clean(row.get("Company Name")),
    }


def member_line(member: dict[str, Any]) -> str:
    return " | ".join(
        [
            clean(member.get("name")),
            clean(member.get("relationshipRole")),
            clean(member.get("email")),
            clean(member.get("phone")),
            clean(member.get("title") or member.get("jobTitle")),
            clean(member.get("description")),
            clean(member.get("lastConversation")),
            clean(member.get("linkedinUrl")),
            clean(member.get("photoUrl")),
        ]
    )


def load_contacts(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def build_indexes(rows: list[dict[str, str]]) -> tuple[dict[str, list[dict[str, str]]], dict[str, list[dict[str, str]]]]:
    by_company: dict[str, list[dict[str, str]]] = {}
    by_deal: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        company_key = compact(row.get("Company Name"))
        deal_key = compact(row.get("Associated Deal"))
        if company_key:
            by_company.setdefault(company_key, []).append(row)
        if deal_key:
            for part in clean(row.get("Associated Deal")).split(";"):
                key = compact(part)
                if key:
                    by_deal.setdefault(key, []).append(row)
    return by_company, by_deal


def matched_contact_rows(
    record: dict[str, Any],
    by_company: dict[str, list[dict[str, str]]],
    by_deal: dict[str, list[dict[str, str]]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    rows.extend(by_company.get(compact(record.get("customer")), []))
    deal_titles = [clean(record.get("dealTitle"))]
    deal_titles.extend(
        clean(deal.get("title"))
        for deal in record.get("deals", [])
        if isinstance(deal, dict) and clean(deal.get("title"))
    )
    for title in deal_titles:
        rows.extend(by_deal.get(compact(title), []))
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = (contact_name(row).lower(), clean(row.get("Email")).lower())
        if key != ("", ""):
            unique[key] = row
    return list(unique.values())


def merge_members(record: dict[str, Any], rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], bool]:
    existing = [
        item
        for item in record.get("teamMembers", [])
        if isinstance(item, dict) and not is_generated_support_player(item)
    ]
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for member in existing:
        key = (clean(member.get("name")).lower(), clean(member.get("email")).lower())
        if key != ("", ""):
            by_key[key] = member
    for row in rows:
        member = row_to_member(row, record)
        if not member:
            continue
        key = (clean(member.get("name")).lower(), clean(member.get("email")).lower())
        current = by_key.get(key, {})
        by_key[key] = {**member, **{k: v for k, v in current.items() if clean(v)}}
    merged = list(by_key.values())
    changed = json.dumps(existing, sort_keys=True) != json.dumps(merged, sort_keys=True)
    return merged, changed


def upsert_db(records: list[dict[str, Any]]) -> int:
    if not records:
        return 0
    conn = azure_client.getConnection()
    try:
        cur = conn.cursor()
        for record in records:
            cur.execute(
                """
                UPDATE atlas_crm_records
                SET data = %s,
                    customer = %s,
                    owner = %s,
                    source_import_batch = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    Jsonb(record),
                    clean(record.get("customer")),
                    clean(record.get("owner")),
                    clean(record.get("sourceImportBatch") or record.get("source_import_batch")),
                    clean(record.get("id")),
                ),
            )
        conn.commit()
        return len(records)
    finally:
        conn.close()


def align_contacts(domain: str, contacts_csv: Path, apply: bool, update_db: bool) -> dict[str, Any]:
    records = json.loads(CRM_RECORDS_PATH.read_text(encoding="utf-8"))
    contact_rows = load_contacts(contacts_csv)
    by_company, by_deal = build_indexes(contact_rows)
    changed_records: list[dict[str, Any]] = []
    matched = 0
    linked_contacts = 0

    for record in records:
        if not isinstance(record, dict) or not is_imported_hubspot_record(record, domain):
            continue
        rows = matched_contact_rows(record, by_company, by_deal)
        if rows:
            matched += 1
            linked_contacts += len(rows)
        merged, changed = merge_members(record, rows)
        if changed:
            record["teamMembers"] = merged
            record["contacts"] = "\n".join(member_line(member) for member in merged)
            record["updatedAt"] = now_utc()
            record["contactAlignment"] = {
                "source": contacts_csv.name,
                "matchedContacts": len(rows),
                "alignedAt": record["updatedAt"],
            }
            changed_records.append(record)

    db_updated = 0
    if apply and changed_records:
        CRM_RECORDS_PATH.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        if update_db:
            db_updated = upsert_db(changed_records)

    return {
        "domain": domain,
        "contacts_csv": str(contacts_csv),
        "hubspot_records_matched": matched,
        "linked_contact_rows": linked_contacts,
        "records_changed": len(changed_records),
        "db_updated": db_updated,
        "applied": apply,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Align Atlas HubSpot contacts from contact export.")
    parser.add_argument("--domain", default="dev", choices=["dev", "engineer", "law", "dental"])
    parser.add_argument("--contacts-csv", type=Path, default=DEFAULT_CONTACTS_CSV)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--db", action="store_true")
    args = parser.parse_args()
    print(json.dumps(align_contacts(args.domain, args.contacts_csv, args.apply, args.db), indent=2))


if __name__ == "__main__":
    main()
