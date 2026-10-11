"""Database-helpers + gebeurtenissen loggen (stdout/bestand, optioneel Kafka)."""
import json
import logging
import os

from sqlalchemy.orm import Session

from .i18n import LANGS
from .models import Media, Post, Section, Slide

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
    """Per volgnummer de tekst in de gekozen taal; ontbreekt die, dan de Nederlandse. 'all' toont altijd."""
    rows = (db.query(Section).filter(Section.page == page, Section.lang.in_([lang, "nl", "all"]))
            .order_by(Section.position, Section.id).all())
    best = {}
    for s in rows:
        if s.lang != "all" and (s.position not in best or s.lang == lang):
            best[s.position] = s
    picked = list(best.values()) + [s for s in rows if s.lang == "all"]
    return sorted(picked, key=lambda s: (s.position, s.id))


def overview(db: Session, labels: dict):
    """Voor het beheer: per pagina en volgnummer welke talen al bestaan."""
    out = []
    for page, label in labels.items():
        by_pos = {}
        for s in db.query(Section).filter(Section.page == page).order_by(Section.position, Section.id):
            by_pos.setdefault(s.position, []).append(s)
        items = []
        for pos, g in sorted(by_pos.items()):
            main = next((s for s in g if s.lang == "nl"), g[0])
            items.append(dict(position=pos, title=main.title, preview=(main.content or "").strip()[:70],
                              langs={s.lang: s for s in g}))
        out.append(dict(page=page, label=label, rows=items))
    return out


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
    """Hero: geüploade media met 'in slideshow'; anders de placeholders uit seed.py."""
    items = [dict(url=f"/media/{m.name}", caption=m.caption, kind=m.kind)
             for m in db.query(Media).filter(Media.in_hero == True).order_by(Media.id)]  # noqa: E712
    if items:
        return items
    return [dict(url=f"/static/images/{s.filename}", caption=s.caption, kind="image")
            for s in db.query(Slide).order_by(Slide.id)]


def posts_for(db: Session, lang: str):
    def q(l):
        return db.query(Post).filter(Post.lang.in_([l, "all"])).order_by(Post.created.desc(), Post.id.desc()).all()
    return q(lang) or q("nl")
