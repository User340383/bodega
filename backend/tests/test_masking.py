"""NFR-717 masking set and the M3 9.5 examples (M3 names this file: backend/tests/test_masking.py).

NFR-717: 100% of phone numbers and email addresses in a 50-case set are masked before
transmission, and 100% of numbers with fewer than 7 digits are left unchanged. "Phone number"
follows the approved M3 9.5 pattern (international, North American incl. 809/829/849, local
555-1234 and unformatted 10-15 digit runs), which replaced the SRS's generic "7+ digits" rule
because that rule erased ordinary sales lines (M3 section 10).
"""
import pytest

from app.privacy import MASK, mask_text

M3_EXAMPLES = ["+1 809 555 1234", "(809) 555-1234", "809-555-1234", "809.555.1234", "555-1234", "8095551234"]

PHONES = M3_EXAMPLES + [
    "+18095551234", "+1 (809) 555-1234", "+1-809-555-1234", "+1.809.555.1234", "+44 207 946 0958",
    "+34 912 345 6789", "+52 555 123 4567", "829-555-1234", "849-555-1234", "(829) 555-1234",
    "(849)555-1234", "829.555.0199", "212-555-0147", "(212) 555-0147", "415.555.0132", "555-0199",
    "4155550132", "18095551234", "448095551234", "+5215551234567", "123456789012345", "+12125550147",
    "+1 212 555 0147", "(305) 555-0110", "305-555-0110",
]
EMAILS = [
    "ana@example.com", "jose.otazu@example.org", "maria_lopez@shop.do", "first.last+sales@example.co.uk",
    "a@b.io", "ventas123@colmado.com.do", "owner-2026@example.net", "Customer@Example.COM",
    "x.y.z@sub.domain.example", "bodega%team@example.com", "pedidos@tienda.mx", "info@example.travel",
    "user.name@example.museum", "j@example.do", "MiCliente@Correo.Example",
    "d.cabrera@example.edu", "l.heyaime@example.edu", "g.marte@example.edu", "sales@example.biz",
]
CONTACTS = PHONES + EMAILS


def test_set_has_fifty_distinct_cases():
    assert len(CONTACTS) == 50 and len(set(CONTACTS)) == 50


@pytest.mark.parametrize("contact", CONTACTS)
def test_every_contact_is_masked_in_context(contact):
    for text in (contact, f"Call {contact} for orders", f"Bread 3 1.50\nCliente: {contact}\nMilk 2 2.25"):
        masked = mask_text(text)
        assert contact not in masked and MASK in masked


@pytest.mark.parametrize("text", [
    "Bread 3 1.50", "Coke 2 1.50 3.00", "Rice (lb) 2.5 x 0.80", "Eggs 12 0.25", "Milk, 2, $2.25",
    "Total 123456", "Sold 999999 units", "2026-10-07", "Batch 12-34", "Price 1,250.75", "Item 42 7 3.5",
    "Plantains 6 0.35 2.10", "100 200 300", "Sardines 1 1.75",
])
def test_numbers_with_fewer_than_seven_digits_and_dates_are_unchanged(text):
    assert mask_text(text) == text


def test_mixed_log_keeps_sales_and_masks_only_contacts():
    log = "Bread 3 1.50\nCall Ana 809-555-1234 or ana@example.com\nMilk 2 2.25"
    assert mask_text(log) == "Bread 3 1.50\nCall Ana [removed] or [removed]\nMilk 2 2.25"
