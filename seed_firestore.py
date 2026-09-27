"""Seed script for DevCraft Firestore database."""
from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-02-390bef78edb6"
COLLECTION_NAME = "dev_tickets"

SEED_TICKETS = [
    {
        "ticket_id": "TICK-101",
        "title": "Refactor auth middleware to support OAuth2 tokens",
        "author": "alex_dev",
        "assigned_to": "sam_lead",
        "status": "in_review",
        "priority": "high",
        "description": "Update JWT parsing and add validation hooks for external identity providers.",
        "created_at": "2026-09-25T10:00:00Z",
    },
    {
        "ticket_id": "TICK-102",
        "title": "Fix memory leak in background worker task queue",
        "author": "maria_eng",
        "assigned_to": "alex_dev",
        "status": "open",
        "priority": "critical",
        "description": "Worker threads do not release database connections on exception.",
        "created_at": "2026-09-26T14:30:00Z",
    },
    {
        "ticket_id": "TICK-103",
        "title": "Add OpenTelemetry tracing for API endpoints",
        "author": "sam_lead",
        "assigned_to": "maria_eng",
        "status": "closed",
        "priority": "medium",
        "description": "Instrument FastAPI routes with standard OpenTelemetry GenAI context propagators.",
        "created_at": "2026-09-24T09:15:00Z",
    },
    {
        "ticket_id": "TICK-104",
        "title": "Implement Conventional Commits validator tool",
        "author": "dev_bot",
        "assigned_to": "unassigned",
        "status": "open",
        "priority": "low",
        "description": "Parse git commit messages and enforce feat/fix/docs prefix structure.",
        "created_at": "2026-09-27T08:00:00Z",
    },
]


def seed_database():
    print(f"Connecting to Firestore for project '{PROJECT_ID}'...")
    db = firestore.Client(project=PROJECT_ID)
    collection_ref = db.collection(COLLECTION_NAME)

    for ticket in SEED_TICKETS:
        doc_ref = collection_ref.document(ticket["ticket_id"])
        doc_ref.set(ticket)
        print(f"  [+] Seeded ticket: {ticket['ticket_id']} - {ticket['title']}")

    print(f"✅ Successfully seeded {len(SEED_TICKETS)} items into '{COLLECTION_NAME}' collection.")


if __name__ == "__main__":
    seed_database()
