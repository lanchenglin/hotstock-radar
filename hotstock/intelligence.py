"""Versioned event clues. Source rank is not truth; no news can loosen a stop."""
import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .service import ServiceError, identifier
from .timeutil import SHANGHAI, now_iso, parse_time

RISK_WORDS = ("减持", "立案", "处罚", "问询", "诉讼", "澄清", "风险提示", "终止", "下修", "未形成收入", "尚未产生收入", "退市", "亏损")
CATALYST_WORDS = ("订单", "中标", "发布", "政策", "标准", "投产", "突破", "签约", "合作", "增长")
THEME_WORDS = {"机器人": ("机器人", "具身智能", "减速器"), "AI漫剧": ("漫剧", "AI短剧", "人工智能短剧"),
               "影视": ("影视", "电影", "票房", "短剧")}


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def safe_url(value):
    if not value:
        return ""
    p = urlsplit(value)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("来源链接必须为无内嵌凭据的 HTTP(S) 地址")
    # Links are retained, not fetched. Drop trackers; reject apparent credentials.
    pairs = parse_qsl(p.query, keep_blank_values=True)
    if any(k.lower() in {"token", "access_token", "api_key", "key", "password"} for k, _ in pairs):
        raise ValueError("来源链接不能包含访问密钥")
    query = urlencode([(k, v) for k, v in pairs if not k.lower().startswith("utm_")])
    return urlunsplit((p.scheme, p.netloc.lower(), p.path, query, ""))


class Article(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=2, max_length=500)
    text: str = Field(default="", max_length=12000)
    url: str = Field(default="", max_length=2000)
    source: str = Field(default="manual", min_length=1, max_length=100)
    kind: Literal["announcement", "news", "policy", "manual"] = "news"
    symbols: list[str] = Field(default_factory=list, max_length=50)
    published_at: str | None = None
    occurred_at: str | None = None
    title_only: bool = False
    related_event_id: str | None = Field(default=None, max_length=100)

    @field_validator("url")
    @classmethod
    def url_valid(cls, value):
        return safe_url(value)

    @field_validator("symbols")
    @classmethod
    def symbols_valid(cls, value):
        if any(not re.fullmatch(r"(?:\d{6}\.(SH|SZ|BJ)|DEMO\d{3})", x) for x in value):
            raise ValueError("证券代码需使用 000001.SZ 格式")
        return sorted(set(value))

    @field_validator("published_at", "occurred_at")
    @classmethod
    def times_valid(cls, value):
        return parse_time(value).isoformat() if value else None


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["pending", "tracking", "reviewed", "disproved", "closed"]
    note: str = Field(min_length=5, max_length=2000)
    evidence_url: str = Field(default="", max_length=2000)

    @field_validator("evidence_url")
    @classmethod
    def url_valid(cls, value):
        return safe_url(value)


class Intelligence:
    def __init__(self, store):
        self.store = store

    def ingest(self, values, mode, known_at=None):
        # known_at is internal only. API clients cannot backdate first discovery.
        seen = known_at or now_iso()
        when = parse_time(seen)
        articles = [Article.model_validate(value).model_dump() for value in values]
        for a in articles:
            if a["published_at"] and parse_time(a["published_at"]) > when:
                raise ServiceError("新闻发布时间晚于系统获取时间；请核对源端时钟")
            if a["occurred_at"] and parse_time(a["occurred_at"]) > when:
                raise ServiceError("未来催化日期请写在正文，不能登记成已发生事件")
            if mode != "demo" and any(s.startswith("DEMO") for s in a["symbols"]):
                raise ServiceError("模拟消息不能写入真实工作区")
        result = {"new_events": 0, "new_versions": 0, "duplicates": 0, "risk_ids": []}
        with self.store.connect() as db:
            def read(key):
                row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
                return json.loads(row[0]) if row else None
            for a in articles:
                normalized = re.sub(r"[\W_]+", "", a["title"], flags=re.UNICODE).lower()
                day = (a["published_at"] or seen)[:10]
                # Conservative exact-title clustering: no speculative AI merging.
                cluster = digest(mode + "|" + day + "|" + normalized + "|" + ",".join(a["symbols"]))
                identity = a["url"] if a["url"] and urlsplit(a["url"]).path not in {"", "/", "/telegraph"} else a["source"] + "|" + normalized + "|" + day
                docid = digest(mode + "|" + identity)
                old = read("intel_doc:" + docid)
                eid = old["event_id"] if old else cluster
                event = read("intel_event:" + eid)
                # Query keyword is not proof; callers only supply symbols with textual/code evidence.
                version = digest(docid + "|" + str(sorted(a.items())))
                if read("intel_version:" + version):
                    result["duplicates"] += 1
                    continue
                text = a["title"] + "\n" + a["text"]
                risks = [word for word in RISK_WORDS if word in text]
                catalysts = [word for word in CATALYST_WORDS if word in text]
                themes = [name for name, terms in THEME_WORDS.items() if any(w in text for w in terms)]
                host = urlsplit(a["url"]).hostname or ""
                official = any(host == h or host.endswith("." + h) for h in ("cninfo.com.cn", "sse.com.cn", "szse.cn")) or host.endswith(".gov.cn")
                if event is None:
                    event = {"id": eid, "created_at": seen, "first_seen_at": seen, "mode": mode,
                             "status": "pending", "symbols": [], "themes": [], "versions": [], "sources": [],
                             "risk_words": [], "review_history": [], "title": a["title"]}
                    result["new_events"] += 1
                was_reviewed = event["status"] in {"reviewed", "closed", "disproved"}
                record = {**a, "id": version, "document_id": docid, "event_id": eid, "created_at": seen,
                          "first_seen_at": old["first_seen_at"] if old else seen, "observed_at": seen,
                          "source_class": "official_domain" if official else "unverified_source",
                          "confidence": "unverified", "risk_words": risks, "catalyst_words": catalysts}
                event.update(title=a["title"], updated_at=seen,
                             symbols=sorted(set(event["symbols"] + a["symbols"])),
                             themes=sorted(set(event["themes"] + themes)),
                             sources=sorted(set(event["sources"] + [a["source"]])),
                             risk_words=sorted(set(event["risk_words"] + risks)))
                if was_reviewed and risks:
                    event["status"] = "pending"
                    event["review_history"].append({"at": seen, "status": "pending", "note": "新版本含风险词，重新待核实"})
                event["versions"].append(version)
                event["latest"] = record
                event["repost_count"] = max(0, len(event["sources"]) - 1)
                event["note"] = "关键词线索，不是已核实事实；来源数量不等于独立确认次数。"
                if a["related_event_id"]:
                    if not read("intel_event:" + a["related_event_id"]) or read("intel_event:" + a["related_event_id"])["mode"] != mode:
                        raise ServiceError("关联事件不存在")
                    event["related_event_id"] = a["related_event_id"]
                self.store.put("intel_doc:" + docid, {"event_id": eid, "first_seen_at": record["first_seen_at"]}, db)
                self.store.put("intel_version:" + version, True, db)
                self.store.put("intel_event:" + eid, event, db)
                self.store.record("intel_version", record, mode, db)
                self.store.record("intel", event, mode, db)
                result["new_versions"] += 1
                if risks and a["symbols"]:
                    result["risk_ids"].append(eid)
        return result

    def events(self, mode, as_of=None, limit=200):
        rows = self.store.records("intel", mode, limit)
        if as_of:
            # Never use later revisions or later review decisions in historical research.
            cutoff = parse_time(as_of)
            output = []
            versions = self.store.records("intel_version", mode, 10000)
            for event in rows:
                visible = [v for v in versions if v["event_id"] == event["id"] and parse_time(v["observed_at"]) <= cutoff]
                if not visible:
                    continue
                e = deepcopy(event)
                visible.sort(key=lambda v: v["observed_at"])
                e.update(title=visible[-1]["title"], updated_at=visible[-1]["observed_at"], sources=sorted({v["source"] for v in visible}),
                         themes=sorted({name for v in visible for name, terms in THEME_WORDS.items() if any(w in v["title"]+v["text"] for w in terms)}),
                         latest=visible[-1], versions=[v["id"] for v in visible],
                         symbols=sorted({s for v in visible for s in v["symbols"]}),
                         risk_words=sorted({s for v in visible for s in v["risk_words"]}))
                e["review_history"] = [r for r in e["review_history"] if parse_time(r["at"]) <= cutoff]
                e["status"] = e["review_history"][-1]["status"] if e["review_history"] else "pending"
                output.append(e)
            return output
        return rows

    def review(self, eid, mode, values):
        review = Review.model_validate(values).model_dump()
        with self.store.connect() as db:
            e = self.store.get("intel_event:" + eid)
            if not e or e["mode"] != mode:
                raise ServiceError("事件不存在于当前工作区", 404)
            review["at"] = now_iso()
            e["review_history"].append(review)
            e.update(status=review["status"], updated_at=review["at"])
            self.store.put("intel_event:" + eid, e, db)
            self.store.record("intel", e, mode, db)
        return e

    def risks(self, mode, clock=None):
        return [e for e in self.events(mode, clock, 2000)
                if e["risk_words"] and e["symbols"] and e["status"] in {"pending", "tracking"}]
