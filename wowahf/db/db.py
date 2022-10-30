import sqlalchemy.exc
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import create_engine
from __main__ import cfg_provider, logger

database = cfg_provider.get("DB_DATABASE")
addr = cfg_provider.get("DB_ADDRESS")
port = cfg_provider.get("DB_PORT")
user = cfg_provider.get("DB_USERNAME")
password = cfg_provider.get("DB_PASSWORD")

engine = None
Base = None
session_generator = None


def initialize():
    global engine, Base, session_generator
    try:
        logger.info("Initializing db {}:{}/{} as user {}...".format(addr, port, database, user))
        engine = create_engine('mysql+mysqlconnector://{user}:{password}@{addr}:{port}/{database}'.format(
            user=user,
            password=password,
            port=port,
            addr=addr,
            database=database
        ))
        Base = declarative_base()
        session_generator = scoped_session(sessionmaker(bind=engine.execution_options(isolation_level='SERIALIZABLE'), autoflush=False))
        import wowahf.db.models
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        logger.error("Could not initialize: {}".format(e.__class__.__name__))
        raise e


def get_session_factory():
    return session_generator


def get_connection():
    return engine.connect()


def get_raw_connection():
    return engine.raw_connection()


def get_session():
    return session_generator()


def get_transaction():
    session = get_session()
    try:
        session_transaction = session.begin()
    except sqlalchemy.exc.InvalidRequestError:
        session_transaction = session.begin_nested()
    if session_transaction is None:
        raise sqlalchemy.exc.InvalidRequestError("Could not create an SQL session.")
    return session, session_transaction


def call_procedure(function_name, params):
    connection = get_raw_connection()
    try:
        cursor = connection.cursor()
        cursor.callproc(function_name, params)
        cursor.close()
        connection.commit()
    finally:
        connection.close()