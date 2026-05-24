import pandas as pd

from src.contacts import load_contacts


def test_load_contacts_uses_legal_name_column(tmp_path):
    path = tmp_path / "contacts.csv"
    pd.DataFrame(
        [{"legal name": "Acme Dispatch", "Phone": "(555) 123-4567"}]
    ).to_csv(path, index=False)

    contacts = load_contacts(path)

    assert contacts == [{"phone": "+15551234567", "name": "Acme Dispatch"}]


def test_load_contacts_matches_phone_column_case_insensitively(tmp_path):
    path = tmp_path / "contacts.csv"
    pd.DataFrame(
        [{"phone number": "555.123.4567", "Company Name": "North Star"}]
    ).to_csv(path, index=False)

    contacts = load_contacts(path)

    assert contacts == [{"phone": "+15551234567", "name": "North Star"}]
