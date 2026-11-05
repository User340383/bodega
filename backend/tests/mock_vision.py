"""Test-only stand-in for vision output. Not part of the application.

The repository has no extraction adapter yet, so this does NOT exercise José's production
extraction route or any real provider interface. It produces the draft format that the
repository accepts (Gabriel's documented lineId/itemName/quantity/unitPrice/bands shape).
Replace it by injecting through the real interface when that adapter lands.
"""


class MockVisionModel:
    """Deterministic: returns the configured rows and records how often it was called."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def extract(self, image_bytes: bytes) -> dict:
        self.calls += 1
        return {'lines': [{
            'lineId': f'line-{index}', 'itemName': item, 'quantity': quantity, 'unitPrice': price,
            'bands': {'itemName': 'HIGH', 'quantity': quantity_band, 'unitPrice': 'HIGH'},
        } for index, (item, quantity, price, quantity_band) in enumerate(self.rows)]}
