import numpy as np
import polars as pl
from research.lead_trader.action_evidence import MarketTape, prebook, priced
from research.lead_trader.action_analysis import multinomial, split


def test_prebook_never_reads_next_fill_or_uses_after_reversal():
    legs=[
        dict(id="a",ms=100,after=-10,before=0,price=2.,basis=2.,clip=20.,action="open", reversal=False),
        dict(id="b",ms=200,after=-6,before=-10,price=1.8,basis=2.,clip=7.2,action="reduce", reversal=False),
        dict(id="c",ms=300,after=0,before=-6,price=1.5,basis=None,clip=9.,action="close", reversal=True),
    ]
    raw={r["id"]:dict(clip=r["clip"],totalPnl=1.) for r in legs}
    x=prebook(legs,raw,2,300)
    assert x["qty"]==-6 and x["trim_streak"]==1 and x["peak_fraction"]==.6
    assert x["prev_reduce_px"]==1.8 and x["prev_add_px"] is None
    assert np.isclose(x["clip_cover"],2/3)
    mark={k:0. for k in ("ret_5s","ret_15s","ret_1m","ret_5m","ret_15m","ret_1h","acceleration_1m",
                   "range_rebound5m","range_drawdown5m","flow_1m","activity_1m","trade_intensity1m",
                   "turn_5s_1m")}
    mark.update(observable_close=1.9,price_source=1,vol_5m=.1)
    y=priced(x,mark,{},None)
    assert y["from_previous"]<0 and y["from_cost"]>0
    assert np.isclose(y["unrealized_usdt"],.6)


def test_market_clock_respects_publication_lag():
    tape=object.__new__(MarketTape)
    tape.start=0
    tape.lag=100
    assert tape.index(5099)==-1
    assert tape.index(5100)==0
    assert tape.index(10100)==1


def test_action_model_intercept_and_disjoint_lifecycles():
    y=np.array([0]*60+[1]*30+[2]*10)
    beta=multinomial(np.ones((100,1)),y,3)
    logits=np.r_[beta.flatten(),0.]
    assert np.allclose(np.exp(logits)/np.exp(logits).sum(),[.6,.3,.1],atol=1e-5)
    t=pl.DataFrame({"symbol":["A","B","A","B"],"life_id":["ax","bx","ax","by"],"ms":[1,2,3,4]})
    for train,test in split(t,"lifecycle"):
        names=t["life_id"].to_numpy()
        assert not set(names[train])&set(names[test])
