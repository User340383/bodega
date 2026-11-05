"""Opt-in local synthetic fixtures. Never runs at application startup."""
from datetime import date, timedelta
from uuid import UUID
from psycopg.types.json import Jsonb
from .database import connect, migrate
from .repository import owner_id


def seed():
    migrate()
    with connect() as conn:
        conn.execute('INSERT INTO owners(id) VALUES (%s) ON CONFLICT DO NOTHING', (owner_id(),))
        for day in range(2):
            sid = UUID(f'10000000-0000-4000-8000-{day + 1:012d}')
            if conn.execute('SELECT 1 FROM submissions WHERE id=%s', (sid,)).fetchone():
                continue
            sale_date = date.today() - timedelta(days=day + 1)
            conn.execute('INSERT INTO submissions(id,owner_id,sale_date) VALUES (%s,%s,%s)', (sid, owner_id(), sale_date))
            conn.execute("UPDATE submissions SET status='CLASSIFYING' WHERE id=%s", (sid,))
            conn.execute("UPDATE submissions SET status='NEEDS_VERIFICATION' WHERE id=%s", (sid,))
            for index, item in enumerate(('Bread', 'Milk')):
                record_id = UUID(f'20000000-0000-4000-8000-{day * 2 + index + 1:012d}')
                conn.execute("INSERT INTO sales_records(id,submission_id,line_no,item_name,quantity,unit_price,source) VALUES (%s,%s,%s,%s,%s,1.50,'MANUAL')", (record_id, sid, index+1, item, day + index + 2))
            conn.execute("UPDATE submissions SET status='CONFIRMED' WHERE id=%s", (sid,))
        sid = UUID('10000000-0000-4000-8000-000000000003')
        if not conn.execute('SELECT 1 FROM submissions WHERE id=%s', (sid,)).fetchone():
            draft = {'lines': [{'lineId': 1, 'itemName': 'Bread', 'quantity': 3, 'unitPrice': '1.50', 'bands': {'itemName': 'HIGH', 'quantity': 'HIGH', 'unitPrice': 'MEDIUM'}}]}
            conn.execute('INSERT INTO submissions(id,owner_id,sale_date,draft_payload) VALUES (%s,%s,%s,%s)', (sid, owner_id(), date.today(), Jsonb(draft)))
            conn.execute("UPDATE submissions SET status='CLASSIFYING' WHERE id=%s", (sid,))
            conn.execute("UPDATE submissions SET status='NEEDS_VERIFICATION' WHERE id=%s", (sid,))
    print('Review draft: 10000000-0000-4000-8000-000000000003')


if __name__ == '__main__':
    seed()
