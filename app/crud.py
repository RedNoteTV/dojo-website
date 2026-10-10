"""Database-helpers + gebeurtenissen loggen (stdout/bestand, optioneel Kafka)."""
import json
import logging
import os

from sqlalchemy.orm import Session

from .models import Section, Slide

log = logging.getLogger("sfdojo")
_producer = None


def log_event(event: str, **data):
    """Gestructureerde log (JSON). Als KAFKA_BROKER gezet is, gaat het ook naar een Kafka-topic."""
    log.info(json.dumps({"event": event, **data}, ensure_ascii=False))
    broker = os.getenv("KAFKA_BROKER")
    if not broker:
        return
    global _producer
    try:
        if _producer is None:
            from kafka import KafkaProducer  # pip install kafka-python
            _producer = KafkaProducer(bootstrap_servers=broker,
                                      value_serializer=lambda v: json.dumps(v).encode())
        _producer.send(os.getenv("KAFKA_TOPIC", "sfdojo-events"), {"event": event, **data})
    except Exception as exc:  # logging mag de site nooit laten crashen
        log.warning("Kafka niet bereikbaar: %s", exc)


def sections_for(db: Session, page: str, lang: str):
    """Secties van een pagina in de gekozen taal (+ 'all'); ontbreekt de taal, dan Nederlands."""
    def q(l):
        return (db.query(Section).filter(Section.page == page, Section.lang.in_([l, "all"]))
                .order_by(Section.position, Section.id).all())
    return q(lang) or q("nl")


def all_sections(db: Session):
    return db.query(Section).order_by(Section.page, Section.lang, Section.position, Section.id).all()


def save_section(db: Session, s, **data):
    if s is None:
        s = Section()
        db.add(s)
    for k, v in data.items():
        setattr(s, k, v)
    db.commit()
    return s


def delete_section(db: Session, s):
    db.delete(s)
    db.commit()


def slides(db: Session):
    return db.query(Slide).order_by(Slide.id).all()
