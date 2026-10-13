"""Database-helpers + gebeurtenissen loggen (stdout/bestand, optioneel Kafka)."""
import json
import logging
import os
import queue
import smtplib
import ssl
import threading
import time
from datetime import datetime, timezone
from email.message import EmailMessage

from sqlalchemy.orm import Session

from .i18n import LANGS
from .models import Media, Post, Section, Slide

log = logging.getLogger("sfdojo")           # alleen gebeurtenissen (zuivere JSON, één regel per event) -> logbestand voor Wazuh
ops = logging.getLogger("sfdojo_ops")       # technische waarschuwingen (alleen console)
_kafka_queue = queue.Queue(maxsize=1000)
_kafka_thread = None
_kafka_lock = threading.Lock()


def _kafka_worker(broker: str, topic: str):
    """Eigen achtergronddraad: Kafka kan traag of onbereikbaar zijn zonder dat de website er iets van merkt."""
    producer, retry_at = None, 0.0
    while True:
        payload = _kafka_queue.get()
        if producer is None:
            if time.time() < retry_at:
                continue                                   # Kafka ligt eruit: dit event staat nog wel in het logbestand
            try:
                from kafka import KafkaProducer  # pip install kafka-python
                producer = KafkaProducer(bootstrap_servers=broker, value_serializer=lambda v: json.dumps(v, default=str).encode())
            except Exception as exc:
                retry_at = time.time() + 60
                ops.warning("Kafka niet bereikbaar (volgende poging over 1 minuut): %s", type(exc).__name__)
                continue
        try:
            producer.send(topic, payload)
        except Exception as exc:
            producer, retry_at = None, time.time() + 60
            ops.warning("Kafka versturen mislukt: %s", type(exc).__name__)


def log_event(event: str, **data):
    """Gestructureerde gebeurtenis als één JSON-regel (de bron van waarheid). Met KAFKA_BROKER krijgt Kafka er een kopie bij."""
    global _kafka_thread
    payload = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **data}
    if "ip" in payload and "srcip" not in payload:
        payload["srcip"] = payload["ip"]            # veldnaam die Wazuh herkent voor 'zelfde bron-IP'
    log.info(json.dumps(payload, ensure_ascii=False, default=str))
    broker = os.getenv("KAFKA_BROKER")
    if not broker:
        return
    with _kafka_lock:
        if _kafka_thread is None:
            _kafka_thread = threading.Thread(target=_kafka_worker, args=(broker, os.getenv("KAFKA_TOPIC", "sfdojo-events")), daemon=True)
            _kafka_thread.start()
    try:
        _kafka_queue.put_nowait(payload)            # nooit wachten: is de wachtrij vol, dan vervalt alleen de Kafka-kopie
    except queue.Full:
        pass


def send_mail(to: str, subject: str, body: str):
    """Verstuurt een e-mail via SMTP_HOST/PORT/USER/PASSWORD/FROM uit .env. Fouten worden gelogd, nooit getoond."""
    try:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = os.environ["SMTP_FROM"], to, subject
        msg.set_content(body)
        host, port = os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "587"))
        ctx = ssl.create_default_context()
        with (smtplib.SMTP_SSL(host, port, timeout=10, context=ctx) if port == 465 else smtplib.SMTP(host, port, timeout=10)) as smtp:
            if port != 465 and os.getenv("SMTP_STARTTLS", "1") == "1":
                smtp.starttls(context=ctx)
            if os.getenv("SMTP_USER"):
                smtp.login(os.environ["SMTP_USER"], os.getenv("SMTP_PASSWORD", ""))
            smtp.send_message(msg)
    except Exception as exc:
        ops.warning("Mail versturen mislukt: %s", type(exc).__name__)
        log_event("mail_failed", error=type(exc).__name__)


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
