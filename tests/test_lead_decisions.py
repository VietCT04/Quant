import numpy as np
import polars as pl
from research.lead_trader.behavior_market import DAY
from research.lead_trader.decision_evidence import history_at, folded_beta, reversal_context
from research.lead_trader.decision_fit import blocks, cohorts, fitting_indices, model, scores


def test_history_never_counts_current_fill_or_gaps():
    rows=[dict(orderUpdateTime=t,side="BUY",totalPnl=0,clip=5) for t in (1000,3000,5000)]
    h,last=history_at(rows,[1000,3000,5000],5000,[[2000,9000]],[2000])
    assert last["orderUpdateTime"]==3000
    assert "prior_orders_15m" not in h and "prior_orders_60m" not in h
    assert history_at(rows,[1000,3000,5000],9000,[[2000,9000]],[2000])==({},None)


def test_chronology_never_reuses_candidate_lifecycle():
    dates=np.repeat(np.arange(10)*DAY,2)
    t=pl.DataFrame(dict(ms=dates,symbol=["A","B"]*10,
                       life_id=["overlap" if i in (4,16) else str(i) for i in range(20)]))
    train,test=next(blocks(t,"chronology","reduce_long"))
    assert dates[train].max()<dates[test].min()
    assert 4 not in train and 16 in test
    for train,test in blocks(t,"unseen_symbol","reduce_long"):
        assert not set(t["symbol"].to_numpy()[train])&set(t["symbol"].to_numpy()[test])


def test_probability_denominator_and_tied_validation():
    t=pl.DataFrame({"ms":[0]*20,"symbol":["S"]*20})
    y=np.array([1,1,1,0]+[0]*16)
    c=cohorts(t,np.ones(20,dtype=bool),np.arange(20)<10,y)
    assert c["baseline"]==.15 and c["action_given_signal"]==.30 and c["lift"]==2
    assert scores(y,np.full(20,.2),True)["ap"]==.15
    x=np.arange(10,dtype=float)
    beta,_=folded_beta(x*2,x,4)
    assert np.isnan(beta[0]) and beta[-1]==2


def test_training_control_weights_preserve_prevalence():
    y=np.zeros(15000)
    y[:50]=1
    idx,weights=fitting_indices(y,np.arange(len(y)),True)
    assert len(idx)<5000 and y[idx].sum()==50
    assert np.isclose(np.dot(y[idx],weights),50)
    assert np.isclose(weights.sum(),len(y))


def test_incoming_reversal_uses_matched_outgoing_before_state():
    common=dict(risk="size",symbol="X",ms=15,decision_book_ms=15,order_id="o")
    close=dict(common,action="reverse_close",direction=1,exposure=4.,after_usdt=0.)
    open_leg=dict(common,action="reverse_open",direction=-1,after_usdt=8.)
    other=dict(open_leg,ms=14)
    out=reversal_context([close,open_leg,other])
    assert out[1]["exposure"]==4 and out[1]["after_usdt"]==8
    assert "exposure" not in out[2]


def test_continuous_size_fit_targets_center_instead_of_large_outlier():
    y=np.array([2.]*35+[20.])
    estimate=model(np.ones((len(y),1)),y,False)[0]
    assert 2<=estimate<2.1
