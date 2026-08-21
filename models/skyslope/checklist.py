from sqlalchemy import Date, Integer, Numeric, String, ForeignKeyConstraint, Column
from sqlalchemy.dialects.postgresql import UUID
from db import Base

class SaleChecklistActivity(Base):
    __tablename__ = "sale_checklist_activity"

    saleguid = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    activityid = Column(String, primary_key=True, nullable=False)
    order = Column("order", Integer, nullable=True)
    activityname = Column(String, nullable=True)
    dateassigned = Column(Date, nullable=True)
    typeid = Column(Integer, nullable=True)
    typename = Column(String, nullable=True)
    status = Column(String, nullable=True)
    help = Column(String, nullable=True)
    modifiedon = Column(Date, nullable=True)


class SaleChecklistDoc(Base):
    __tablename__ = "sale_checklist_doc"

    docid = Column(String, primary_key=True, nullable=False)
    saleguid = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    activityid = Column(String, nullable=True)
    name = Column(String, nullable=True)
    url = Column(String, nullable=True)
    documentservicekey = Column(String, nullable=True)
    modifieddate = Column(Date, nullable=True)
    uploaddate = Column(Date, nullable=True)
    filename = Column(String, nullable=True)
    extension = Column(String, nullable=True)
    filesize = Column(Numeric, nullable=True)
    pages = Column(Integer, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["activityid", "saleguid"],
            ["sale_checklist_activity.activityid", "sale_checklist_activity.saleguid"],
            ondelete="CASCADE",
        ),
    )


class SaleChecklistActivityDocs(Base):
    __tablename__ = "sale_checklist_activity_docs"

    saleguid = Column(UUID(as_uuid=True), primary_key=True, nullable=False)
    activityid = Column(String, primary_key=True, nullable=False)
    filename = Column(String, primary_key=True, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["activityid", "saleguid"],
            ["sale_checklist_activity.activityid", "sale_checklist_activity.saleguid"],
            ondelete="CASCADE",
        ),
    )


class SaleChecklistComment(Base):
    __tablename__ = "sale_checklist_comment"

    id = Column(Integer, primary_key=True, autoincrement=True)
    activityid = Column(String, nullable=True)
    saleguid = Column(UUID(as_uuid=True), nullable=False)
    comment = Column(String, nullable=True)
    createdon = Column(Date, nullable=True)
    createdby = Column(String, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["activityid", "saleguid"],
            ["sale_checklist_activity.activityid", "sale_checklist_activity.saleguid"],
            ondelete="CASCADE",
        ),
    )