import datetime
from blizzardapi import BlizzardApi
from __main__ import logger, cfg_provider, run_uuid

from wowahf.db.db import get_transaction, call_procedure
from wowahf.db.models import Auction, Item, Entry
from wowahf.db.models.Run import Run

post_run_procedures = {
    "v2_post_parser_run": []
}

blizzard_connection = None
realm = cfg_provider.get("REALM")
region = cfg_provider.get("REGION")
locale = cfg_provider.get("LOCALE")
classic = cfg_provider.get("IS_CLASSIC")
fetch_time = datetime.datetime.now()

entries_processed = 0
errors = 0
items_added = 0


def get_auctions():
    global blizzard_connection
    logger.info("Getting auctions...")
    logger.info("Gathering configuration...")

    api_client_id = cfg_provider.get("CLIENT_ID")
    api_secret_id = cfg_provider.get("SECRET_ID")
    auctions_to_parse = cfg_provider.get("AUCTIONS")

    auction_data = {}
    logger.info("Region: {}; Realm: {}; Locale: {}.".format(realm, region, locale))
    auction_ids = {}

    logger.info("Connecting to blizzard...")
    blizzard_connection = BlizzardApi(api_client_id, api_secret_id)

    logger.info("Getting realm id for realm {}...".format(realm))
    realm_id = blizzard_connection.wow.game_data.get_realm(region, locale, realm, is_classic=classic)["id"]

    logger.info("Getting auction ids...")
    auctions = blizzard_connection.wow.game_data.get_auction_house_index(region, locale, realm_id)

    for anauc in auctions["auctions"]:
        if anauc["name"] in auctions_to_parse:
            auction_ids[anauc["name"]] = anauc["id"]

    for auc_name, auc_id in auction_ids.items():
        logger.info("Getting auction data for auction \"{}\"".format(auc_name))
        auction_data[auc_id] = {"name": auc_name}
        auction_data[auc_id]["data"] = blizzard_connection.wow.game_data.get_auctions_for_auction_house(region, locale, realm_id, auc_id)["auctions"]

    return auction_data


def add_auctions(auction_data):
    for anauc_id, anauc in auction_data.items():
        session, session_transaction = get_transaction()
        with session_transaction:
            auction = session.query(Auction).filter_by(name=anauc["name"]).first()
            if auction is None:
                session.add(Auction(
                    auc_id=anauc_id,
                    name=anauc["name"]
                ))


def add_new_items(auction_data):
    logger.info("Getting all items from database...")
    known_item_ids = []
    items_to_add = []
    session, session_transaction = get_transaction()
    with session_transaction:
        known_item_ids = [x[0] for x in session.query(Item.item_id).all()]
    logger.info("Database has {} items.".format(len(known_item_ids)))
    for anauc_id, anauc in auction_data.items():
        for anitem in anauc["data"]:
            item_id = anitem["item"]["id"]
            if item_id not in known_item_ids and item_id not in items_to_add:
                items_to_add.append(item_id)
    logger.info("{} new items will be added...".format(len(items_to_add)))
    session, session_transaction = get_transaction()
    with session_transaction:
        for anitem in items_to_add:
            add_item(anitem, session)


def add_item(item_id, session):
    global items_added, errors
    try:
        item_obj = blizzard_connection.wow.game_data.get_item(region, locale, item_id, is_classic=classic)
        session.add(Item(
            item_id=item_obj["id"],
            name=item_obj["name"],
            quality=item_obj["quality"]["type"],
            item_class=item_obj["item_class"]["name"],
            item_subclass=item_obj["item_subclass"]["name"],
            vendor_buy=item_obj["purchase_price"],
            vendor_sell=item_obj["sell_price"],
            level=item_obj["level"],
        ))
        logger.info("A new item was added: \"{}\" (id: {})".format(item_obj["name"], item_id))
        items_added += 1
    except Exception as e:
        logger.exception(e)
        logger.warning("Could not add an item: Item ID: {}; Blizzard DB response:\n{}".format(item_id, item_obj))
        errors += 1


def populate_database(auction_data):
    global entries_processed, errors
    add_auctions(auction_data)
    add_new_items(auction_data)
    session, session_transaction = get_transaction()
    logger.info("Adding auction lots...")
    with session_transaction:
        for anauc_id, anauc in auction_data.items():
            logger.info("Parsing auction \"{}\"...".format(anauc["name"]))
            for anitem in anauc["data"]:
                # Add auction entry
                auction_entry_id = anitem["id"]
                logger.debug("Adding auction data for auction {}".format(auction_entry_id))
                try:
                    session.add(Entry(
                        item_id=anitem["item"]["id"],
                        entry_id=auction_entry_id,
                        auction=anauc_id,
                        bid=anitem["bid"],
                        buyout=anitem["buyout"],
                        quantity=anitem["quantity"],
                        time_left=anitem["time_left"],
                    ))
                    entries_processed += 1
                except Exception as e:
                    logger.exception(e)
                    errors += 1
        logger.info("Done. Completing transaction...")


def run_post_sql():
    global errors
    logger.info("Running post-run procedures...")
    try:
        for name, params in post_run_procedures.items():
            logger.info("Calling {}...".format(name))
            call_procedure(name, params)
    except Exception as e:
        logger.error("Error while executing a procedure. Procedure: {}, parameters: {}".format(name, params))
        logger.exception(e)
        errors += 1


def finalize():
    logger.info("Writing run data to the database...")
    session, session_transaction = get_transaction()
    with session_transaction:
        # Run report
        current_run = session.query(Run).filter_by(uuid=run_uuid).first()
        current_run.end_time = datetime.datetime.now()
        current_run.errors = errors
        current_run.entries_processed = entries_processed
        current_run.items_added = items_added

