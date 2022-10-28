from sqlalchemy import Column, Integer, ForeignKey, DateTime, Boolean, VARCHAR
from wowahf.db.db import Base


class Auction(Base):
    __tablename__ = 'auctions'

    id = Column(Integer, nullable=False, autoincrement=True, primary_key=True)
    auc_id = Column(Integer, nullable=False)
    name = Column(VARCHAR(32), nullable=False)
