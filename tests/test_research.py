from copy import deepcopy
from datetime import datetime, timedelta
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from hotstock.app import create_app
from hotstock.intelligence import Article, Intelligence
from hotstock.models import Bundle
from hotstock.public_research import collect
from hotstock.providers.common import ProviderError
from hotstock.shared_market import SharedMarket
from hotstock.storage import Store
from hotstock.timeutil import SHANGHAI

CLOCK = datetime(2026, 10, 19, 10, 30, tzinfo=SHANGHAI)


def article(**values):
    return {"title": "示例公司收到监管问询", "text": "本文仅为测试样本，不代表真实事件。",
            "symbols": ["300115.SZ"], "url": "https://example.com/a/1", "source": "fixture",
            "published_at": "2026-10-19T09:00:00+08:00", **values}


@pytest.fixture
def intel(tmp_path):
    return Intelligence(Store(tmp_path / "test.db"))


def test_same_batch_duplicates_and_source_count(intel):
    a = article()
    result = intel.ingest([a, a, article(url="https://example.net/b", source="转载站")], "live", CLOCK.isoformat())
    assert result["new_events"] == 1
    assert result["new_versions"] == 2
    assert result["duplicates"] == 1
    event = intel.events("live")[0]
    assert len(event["versions"]) == 2
    assert event["repost_count"] == 1
    assert event["latest"]["confidence"] == "unverified"


def test_no_clustering_across_companies(intel):
    intel.ingest([article(), article(url="https://example.com/2", symbols=["000001.SZ"])], "live", CLOCK.isoformat())
    assert len(intel.events("live")) == 2


def test_version_retains_first_seen_and_review_reopens(intel):
    intel.ingest([article()], "live", CLOCK.isoformat())
    e = intel.events("live")[0]
    intel.review(e["id"], "live", {"status": "reviewed", "note": "已核对测试来源原文"})
    intel.ingest([article(text="新增监管问询内容")], "live", (CLOCK + timedelta(minutes=5)).isoformat())
    e = intel.events("live")[0]
    assert e["first_seen_at"] == CLOCK.isoformat()
    assert len(e["versions"]) == 2 and e["status"] == "pending"


def test_historical_view_excludes_future_revision(intel):
    intel.ingest([article(title="公司新订单发布", text="旧版本", url="https://example.com/one")], "live", CLOCK.isoformat())
    intel.ingest([article(title="公司发生诉讼", text="未来版本", url="https://example.com/one")], "live", (CLOCK+timedelta(hours=1)).isoformat())
    event = intel.events("live", CLOCK.isoformat())[0]
    assert event["title"] == "公司新订单发布" and event["latest"]["text"] == "旧版本"
    assert event["risk_words"] == []


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'https://u:p@host/a', 'https://host/a?token=abc'])
def test_dangerous_source_link_rejected(url):
    with pytest.raises(ValueError):
        Article(**article(url=url))


def test_missing_publish_time_not_fabricated(intel):
    intel.ingest([article(published_at=None)], "live", CLOCK.isoformat())
    e = intel.events("live")[0]
    assert e["latest"]["published_at"] is None
    assert e["first_seen_at"] == CLOCK.isoformat()


def test_official_domain_does_not_verify_claim(intel):
    intel.ingest([article(url="https://static.cninfo.com.cn/test", kind="announcement", title_only=True)], "live", CLOCK.isoformat())
    e = intel.events("live")[0]
    assert e["latest"]["source_class"] == "official_domain"
    assert e["latest"]["confidence"] == "unverified"


def test_future_and_demo_rejected(intel):
    with pytest.raises(ValueError):
        intel.ingest([article(published_at="2026-10-20T09:00:00+08:00")], "live", CLOCK.isoformat())
    with pytest.raises(ValueError):
        intel.ingest([article(symbols=["DEMO001"])], "live", CLOCK.isoformat())
    assert intel.events("live") == []


def shared_fixture(stale=False, adjustment="qfq"):
    calls=[]
    def get(url, **kwargs):
        calls.append(url)
        assert kwargs['headers']['Authorization']=='Bearer fixture-token'
        if url.endswith('/ready'): return {'status':'ready'}
        if '/api/quote?' in url:
            return {'symbol':'300115.SZ','name':'测试证券','source':'tencent','datetime':CLOCK.isoformat(),
                    'last':20.0,'previous_close':19.0,'volume':1000,'amount':20000,'stale':stale}
        if '/api/bars?' in url:
            return {'bars':[{'datetime':'2026-10-16T15:00:00+08:00','open':18,'high':20,'low':17,'close':19,'volume':1000,'amount':19000}],
                    'meta':{'sources':['baostock','tencent'],'interval':'1d','volume_unit':'share','amount_unit':'CNY','price_adjustment':adjustment,'stale':False}}
        raise AssertionError('Unexpected endpoint')
    opts={'base_url':'http://127.0.0.1:207','token':'fixture-token','symbols':['300115.SZ'],'max_symbols':2}
    meta={'themes':[{'id':'t','name':'测试题材','members':['300115.SZ','000001.SZ'], 'known_at':CLOCK.isoformat(),'membership_quality':'current_snapshot','evidence':[]}],
          'calendar':['2026-10-16','2026-10-19'],'calendar_confirmed':True,'observed_at':CLOCK.isoformat()}
    return get,calls,opts,meta


def test_shared_contract_and_history_cache():
    get,calls,opts,meta=shared_fixture()
    reader=SharedMarket()
    b=reader.fetch(opts,meta,CLOCK,request_fn=get,clock_fn=lambda:CLOCK)
    Bundle.model_validate(b)
    assert b['quotes'][0]['volume']==1000
    assert b['quotes'][0]['quote_time']==CLOCK.isoformat()
    assert b['quotes'][0]['limit_up'] is None
    assert len(b['themes'][0]['members'])==2  # denominator never becomes sample size
    reader.fetch(opts,meta,CLOCK,request_fn=get,clock_fn=lambda:CLOCK)
    assert sum('/api/bars?' in url for url in calls)==1
    assert all('/api/quote?' in url or '/api/bars?' in url or url.endswith('/ready') for url in calls)


def test_stale_quote_never_renewed():
    get,_,opts,meta=shared_fixture(stale=True)
    b=SharedMarket().fetch(opts,meta,CLOCK,request_fn=get,clock_fn=lambda:CLOCK)
    assert b['quotes'][0]['time_quality']=='unknown'
    assert b['quotes'][0]['upstream_stale']


def test_unknown_adjustment_excludes_history():
    get,_,opts,meta=shared_fixture(adjustment='unknown')
    b=SharedMarket().fetch(opts,meta,CLOCK,request_fn=get,clock_fn=lambda:CLOCK)
    assert not b['bars']


def test_metadata_fresh_fetch_does_not_renew_old_members():
    get,_,opts,meta=shared_fixture()
    meta['themes'][0]['known_at']='2026-10-01T09:00:00+08:00'
    b=SharedMarket().fetch(opts,meta,CLOCK,request_fn=get,clock_fn=lambda:CLOCK)
    assert b['themes'][0]['membership_quality']=='unknown'


def test_no_token_no_network():
    get,calls,opts,meta=shared_fixture()
    opts['token']=''
    with pytest.raises(ProviderError): SharedMarket().fetch(opts,meta,CLOCK,request_fn=get)
    assert calls==[]


def test_api_mode_auth_and_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKET_MCP_TOKEN','PRIVATE_FIXTURE_TOKEN')
    with TestClient(create_app(tmp_path,start_worker=False,api_key='test-key')) as c:
        assert c.get('/api/research').status_code==401
        c.headers['X-API-Key']='test-key'
        assert c.post('/api/research/collect/news',json={}).status_code==422
        c.put('/api/config',json={'provider':'tencent_baostock','mode':'live'})
        assert c.get('/api/config').json()['provider_options']['has_token']
        assert 'PRIVATE_FIXTURE_TOKEN' not in c.get('/api/export').text
        a=article(published_at=None)
        assert c.post('/api/research/import',json={'items':[a]}).status_code==200
        assert c.get('/api/research').json()['risk_count']==1
        e=c.get('/api/research').json()['events'][0]
        assert c.patch('/api/research/events/'+e['id'],json={'status':'reviewed','note':'人工核查完毕，记录结果'}).status_code==200
        assert c.get('/api/research').json()['risk_count']==0
        assert c.post('/api/research/events/'+e['id']+'/summarize',json={}).status_code==422
        assert c.get('/research').status_code==200
        assert 'research-link.js' in c.get('/').text


def test_news_cannot_loosen_stop_or_edit_positions(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as c:
        s=c.app.state.service
        before=s.dashboard()
        candidate=next(x for x in before['analysis']['candidates'] if x['status'] not in {'blocked','expired'})
        plan=s.create_plan(candidate['id'])
        stop=plan['initial_stop_price']
        s.intel.ingest([article(symbols=[candidate['symbol']],published_at=None)],'demo','2026-10-19T09:00:00+08:00')
        after=s.dashboard()
        assert next(p for p in after['plans'] if p['id']==plan['id'])['can_execute'] is False
        assert next(p for p in after['plans'] if p['id']==plan['id'])['initial_stop_price']==stop
        assert after['positions']==before['positions']
        assert s.store.records('trade','demo')==[]
        assert c.get('/api/plans').json()['items'][0]['can_execute'] is False


def test_job_partial_failures_and_no_demo_fallback(tmp_path,monkeypatch):
    monkeypatch.setattr('hotstock.research.run_collection',lambda kind,cfg: {'sources':{'cls':{'state':'error'}},'articles':[],'warnings':['failed']})
    with TestClient(create_app(tmp_path,start_worker=False)) as c:
        c.put('/api/config',json={'provider':'tencent_baostock'})
        assert c.post('/api/research/collect/news',json={}).status_code==202
        for _ in range(100):
            state=c.get('/api/research').json()['jobs']['news']
            if state['state']!='running':break
            time.sleep(.01)
        assert state['state']=='error' and state['coverage_stale']
        assert c.get('/api/research').json()['events']==[]


class Frame:
    def __init__(self,rows):self.rows=rows
    def to_dict(self,orient):return self.rows


class FakeAK:
    def stock_board_concept_name_em(self):return Frame([{'板块代码':'BK1','板块名称':'机器人','涨跌幅':2}])
    def stock_board_concept_cons_em(self,symbol):return Frame([{'代码':'300115','涨跌幅':3},{'代码':'000001','涨跌幅':1}])
    def tool_trade_date_hist_sina(self):return Frame([{'trade_date':'2020-01-01'},{'trade_date':'2099-12-31'}])
    def stock_zt_pool_em(self,date):return Frame([{'代码':'300115','名称':'样例','连板数':1}])
    def stock_info_global_cls(self,symbol):return Frame([{'标题':'示例快讯','内容':'仅测试','发布日期':'2020-01-01','发布时间':'09:00:00'}])
    def stock_news_em(self,symbol):return Frame([{'新闻标题':'不含股票代码的结果','新闻内容':'不应强行关联个股','新闻链接':'https://example.com/news'}])
    def stock_zh_a_disclosure_report_cninfo(self,**kwargs):return Frame([{'代码':'300115','公告标题':'公司风险提示','公告时间':'2020-01-01','公告链接':'https://static.cninfo.com.cn/sample.pdf'}])


def test_public_topic_contract():
    data=collect('topics',{},FakeAK())
    assert data['themes'][0]['members']==['300115.SZ','000001.SZ']
    assert data['themes'][0]['membership_quality']=='current_snapshot'
    assert data['limit_up_pool'][0]['boards']==1


def test_public_news_search_is_not_evidence():
    data=collect('news',{'symbols':['300115.SZ']},FakeAK())
    news=next(a for a in data['articles'] if a['source']=='东方财富聚合')
    assert news['symbols']==[]
    ann=next(a for a in data['articles'] if a['kind']=='announcement')
    assert ann['title_only'] and ann['symbols']==['300115.SZ']
    for a in data['articles']:Article.model_validate(a)


def test_ai_rejects_fabricated_evidence(tmp_path,monkeypatch):
    monkeypatch.setenv('HOTSTOCK_LLM_ENABLED','1');monkeypatch.setenv('HOTSTOCK_LLM_BASE_URL','http://127.0.0.1:9999/v1')
    monkeypatch.setenv('HOTSTOCK_LLM_MODEL','fake-model')
    monkeypatch.setattr('hotstock.research.request_json',lambda *a,**k: {'choices':[{'message':{'content':json.dumps({'summary':'bad','evidence_quotes':['invented evidence'],'uncertainties':[]})}}]})
    with TestClient(create_app(tmp_path,start_worker=False)) as c:
        c.put('/api/config',json={'mode':'live'})
        c.post('/api/research/import',json={'items':[article(published_at=None)]})
        e=c.get('/api/research').json()['events'][0]
        assert c.post('/api/research/events/'+e['id']+'/summarize',json={}).status_code==502
        assert c.get('/api/research').json()['events'][0]['status']=='pending'
