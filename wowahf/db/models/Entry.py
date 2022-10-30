from sqlalchemy import Column, Integer, DateTime, VARCHAR, ForeignKey
from __main__ import run_uuid
from wowahf.db.db import Base


class Entry(Base):
    __tablename__ = 'z_entries_{}'.format(run_uuid)
    __table_args__ = {
        'mysql_row_format': 'COMPRESSED'
    }
    id = Column(Integer, nullable=False, autoincrement=True, primary_key=True)
    auction = Column(Integer)
    entry_id = Column(Integer, nullable=False, primary_key=True)
    item_id = Column(Integer)
    bid = Column(Integer, nullable=False)
    buyout = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)
    time_left = Column(VARCHAR(16), nullable=False)
