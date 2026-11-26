#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <memory>
#include <stdexcept>
#include <type_traits>
#include <vector>

#ifdef _WIN32
#define EXPORTED extern "C" __declspec(dllexport)
#else
#define EXPORTED extern "C" __attribute__((visibility("default")))
#endif

using Time = std::int64_t;
constexpr double epsilon = 1e-11;
constexpr double unset = std::numeric_limits<double>::quiet_NaN();
static bool price_ok(double v) { return std::isfinite(v) && v > 0; }

struct Config {
    double initial_wallet, fee_bps, spread_bps, slippage_bps, impact_bps, participation;
    double leverage, maintenance_rate, max_gross_leverage, max_symbol, max_gross;
    double max_adjustment, min_adjustment, drawdown_limit;
    Time interval, lag, latency, rebalance_ticks, max_positions, snapshot_ticks;
    Time trade_mark_fallback, logging;
};

struct Rule { double step, minimum; };
struct Bar { double close, execution, volume, mark, target; };
struct Market { double close, execution, volume, mark; };
struct Settlement { Time ms, symbol; double rate, price; };
struct Order {
    Time ms, bar_start, decision, symbol, reason;
    double target, requested, filled, reference, price, fee, slippage, realized;
    double closed, closed_net, hold_ms, after, entry;
    Time id, kind;
    double requested_notional, rounded, buying_power, intent_size;
};
// kind: 0 signed notional, 1 signed base quantity, 2 close, 3 reduce.
struct Request { Time due, id, symbol, kind, decision; double size; };
struct Cashflow { Time ms, symbol; double qty, mark, rate, payment; };
struct Target { Time ms, symbol; double notional; };
struct Equity {
    Time ms, uncertain_positions, maintenance_breach;
    double wallet, realized, unrealized, equity, gross, net, initial, maintenance, available;
    double fees, slippage, funding, turnover, risk_drawdown;
};
struct RiskSummary { Time maintenance_ticks, uncertain_ticks, suspended_ticks; double peak_gross; };
struct Position {
    double qty = 0, entry = 0, mark = unset, target = unset, entry_clock = 0, entry_fees = 0;
    double realized = 0, fees = 0, slippage = 0, funding = 0, turnover = 0;
    double long_realized = 0, short_realized = 0, long_cost = 0, short_cost = 0;
    double long_funding = 0, short_funding = 0;
    double long_fee = 0, short_fee = 0;
    Time decision = 0, gap_ticks = 0, held_unmarked_ticks = 0, fallback_ticks = 0, orders = 0, fills = 0;
    bool pending = false;
};
// Layout exported separately so the C ABI does not depend on C++ bool padding.
struct PositionView {
    double qty, entry, mark, target, realized, unrealized, fees, slippage, funding, turnover;
    double long_realized, short_realized, long_cost, short_cost, long_funding, short_funding;
    double long_fee, short_fee;
    Time gap_ticks, held_unmarked_ticks, fallback_ticks, orders, fills;
};

#include "order_program.hpp"
#include "shock_features.hpp"

class Strategy {
public:
    virtual ~Strategy() = default;
    virtual double target(std::size_t symbol, bool has_target) const = 0;
};
class BuyAndHold final : public Strategy {
    std::vector<double> allocations;
public:
    BuyAndHold(const double* values, std::size_t n) : allocations(values, values + n) {}
    double target(std::size_t symbol, bool has_target) const override {
        return has_target ? unset : allocations[symbol];
    }
};

class Engine {
    Config cfg;
    std::vector<Rule> rules;
    std::vector<Position> book;
    std::vector<unsigned char> fresh;
    BuyAndHold* native = nullptr;
    Equity current{};
    RiskSummary risk{};
    double peak, wallet, fees = 0, slippage = 0, realized = 0, funding = 0, turnover = 0, worst_dd = 0;
    Time count = 0, last = -1;
    bool suspended = false;
    double order_step = 0;
    bool liquidation = false;
    std::unique_ptr<OrderProgram> program;
    std::vector<Request> program_orders;
public:
    std::vector<Order> orders;
    std::vector<Cashflow> cashflows;
    std::vector<Target> targets;
    std::vector<Equity> curve;
    Engine(const Config& c, const Rule* r, std::size_t n, const double* native_target)
        : cfg(c), rules(r, r+n), book(n), fresh(n), peak(c.initial_wallet), wallet(c.initial_wallet) {
        if (native_target) native = new BuyAndHold(native_target, n);
    }
    ~Engine() { delete native; }
    Equity final() const { return current; }
    RiskSummary risk_summary() const { return risk; }
    void positions(PositionView* out) const {
        for (std::size_t i = 0; i < book.size(); ++i) {
            const auto& p = book[i];
            out[i] = {p.qty, p.entry, p.mark, p.target, p.realized,
                p.qty && price_ok(p.mark) ? p.qty * (p.mark-p.entry) : 0,
                p.fees, p.slippage, p.funding, p.turnover, p.long_realized, p.short_realized,
                p.long_cost, p.short_cost, p.long_funding, p.short_funding, p.long_fee, p.short_fee,
                p.gap_ticks, p.held_unmarked_ticks, p.fallback_ticks, p.orders, p.fills};
        }
    }
    void settle(const Settlement& f) {
        auto& p = book.at(f.symbol);
        if (!p.qty) return;
        double cash = -p.qty * f.price * f.rate;
        wallet += cash;
        funding += cash;
        p.funding += cash;
        (p.qty > 0 ? p.long_funding : p.short_funding) += cash;
        if (cfg.logging == 1 || order_step) cashflows.push_back({f.ms, f.symbol, p.qty, f.price, f.rate, cash});
    }
    template<class Input>
    void adjust(std::size_t i, const Input& b, Time start, Time now, double& gross, double& upnl, Time& active, Time uncertain) {
        auto& p = book[i];
        const auto& rule = rules[i];
        double ref = b.execution;
        double target = suspended ? 0 : std::clamp(p.target, -cfg.max_symbol, cfg.max_symbol);
        double desired = target / ref;
        double wanted = desired - p.qty;
        if (std::abs(wanted) <= epsilon || (target != 0 && !suspended && std::abs(wanted * ref) < cfg.min_adjustment)) {
            p.pending = false;
            return;
        }
        Order log{now, start, p.decision, static_cast<Time>(i), 0, target, wanted, 0, ref, unset, 0, 0, 0,
                  0, 0, 0, p.qty, p.entry};
        ++p.orders;
        double old = p.qty, mark = p.mark;
        double reducing = wanted * old < 0 ? std::min(std::abs(old), std::abs(wanted)) : 0;
        double other = std::max(0.0, gross - std::abs(old * mark));
        const double equity = wallet + upnl;
        double risk_equity = std::max(0.0, std::min(equity, wallet + upnl - std::abs(old) * std::max(0.0, mark - ref)));
        double gross_cap = std::min({cfg.max_gross, risk_equity * cfg.max_gross_leverage,
                                    risk_equity * cfg.leverage});
        double sym_cap = std::min(cfg.max_symbol / std::max(mark, ref),
                                  std::max(0.0, gross_cap - other) / mark);
        bool can_open = !suspended && !uncertain && fresh[i]
                        && (old != 0 || active < cfg.max_positions);
        double base = std::abs(old) - reducing;
        double add = can_open ? std::min(std::abs(wanted) - reducing, std::max(0.0, sym_cap - base)) : 0;
        double amount = std::min({reducing + add, b.volume * cfg.participation, cfg.max_adjustment / ref});
        if (rule.step > 0) amount = std::floor(amount / rule.step + 1e-10) * rule.step;
        double abs_wanted = std::abs(wanted);
        if (std::abs(amount - reducing) < epsilon) amount = reducing;
        if (amount <= epsilon || (amount * ref < rule.minimum && amount > reducing + epsilon)) {
            log.reason = reducing + add <= epsilon ? 2 : 3;
            if (std::abs(wanted) < rule.step || (reducing == 0 && std::abs(wanted) * ref < rule.minimum))
                p.pending = false;
            if (cfg.logging == 1) orders.push_back(log);
            return;
        }
        double delta = std::copysign(amount, wanted);
        double closed = delta * old < 0 ? std::min(std::abs(old), amount) : 0;
        double opened = amount - closed;
        double cost_bps = cfg.spread_bps / 2 + cfg.slippage_bps
                        + cfg.impact_bps * std::sqrt(amount / b.volume);
        double fill = ref * (1 + std::copysign(cost_bps / 10000, delta));
        if (!price_ok(fill)) { log.reason = 3; if (cfg.logging == 1) orders.push_back(log); return; }
        double execution_cost = amount * std::abs(fill - ref);
        double fee = amount * fill * cfg.fee_bps / 10000;
        // Risk-reducing trades can finish during a margin breach. Avoid adding
        // exposure whose modeled fill moves the portfolio beyond its limits.
        if (opened > epsilon && (other + std::abs((old + delta) * mark) >
                std::max(0.0, equity - fee + delta * (mark - fill)) * std::min(cfg.leverage, cfg.max_gross_leverage) + epsilon)) {
            log.reason = 2;
            if (cfg.logging == 1) orders.push_back(log);
            return;
        }
        book_fill(i, log, delta, fill, execution_cost, fee, gross, upnl, active, true);
    }
    void book_fill(std::size_t i, Order log, double delta, double fill, double execution_cost,
                   double fee, double& gross, double& upnl, Time& active, bool legacy) {
        auto& p = book[i];
        const double old = p.qty, mark = p.mark, amount = std::abs(delta);
        const double closed = delta * old < 0 ? std::min(std::abs(old), amount) : 0;
        const double opened = amount - closed, other = std::max(0.0, gross - (old ? std::abs(old * mark) : 0));
        const double abs_wanted = std::abs(log.requested), ref = log.reference;
        const Time now = log.ms;
        double pnl = closed * (fill - p.entry) * (old >= 0 ? 1 : -1);
        double entry_fee = closed ? p.entry_fees * (closed / std::abs(old)) : 0;
        double held = closed ? now - p.entry_clock : 0;
        p.entry_fees -= entry_fee;
        if (closed) {
            (old > 0 ? p.long_realized : p.short_realized) += pnl;
            (old > 0 ? p.long_cost : p.short_cost) += (fee + execution_cost) * closed / amount;
            (old > 0 ? p.long_fee : p.short_fee) += fee * closed / amount;
        }
        if (opened) {
            (delta > 0 ? p.long_cost : p.short_cost) += (fee + execution_cost) * opened / amount;
            (delta > 0 ? p.long_fee : p.short_fee) += fee * opened / amount;
        }
        double old_unrealized = old * (mark - p.entry);
        double remains = std::abs(old) - closed;
        p.qty = old + delta;
        if (std::abs(p.qty) <= epsilon) { p.qty = 0; p.entry = 0; p.entry_fees = 0; p.entry_clock = 0; }
        else if (opened) {
            p.entry = (remains * p.entry + opened * fill) / (remains + opened);
            p.entry_clock = (remains * p.entry_clock + opened * static_cast<double>(now)) / (remains + opened);
            p.entry_fees += fee * opened / amount;
        }
        active += static_cast<Time>(p.qty != 0) - static_cast<Time>(old != 0);
        wallet += pnl - fee;
        realized += pnl;
        fees += fee;
        slippage += execution_cost;
        turnover += amount * ref;
        p.realized += pnl; p.fees += fee; p.slippage += execution_cost; p.turnover += amount * ref;
        ++p.fills;
        gross = other + std::abs(p.qty * mark);
        upnl += p.qty * (mark - p.entry) - old_unrealized;
        if (legacy && abs_wanted - amount <= epsilon) p.pending = false;
        if (cfg.logging == 1 || !legacy) {
            if (legacy) log.reason = p.pending ? 1 : 0;
            log.filled = delta; log.price = fill; log.fee = fee; log.slippage = execution_cost;
            log.realized = pnl; log.closed = closed; log.closed_net = pnl - entry_fee - fee * closed / amount;
            log.hold_ms = held; log.after = p.qty; log.entry = p.entry;
            orders.push_back(log);
        }
    }
    void orders_config(double step) {
        order_step = step;
        current.wallet = current.equity = current.available = wallet;
    }

    void totals(double& gross, double& upnl, Time& active) const {
        gross = upnl = 0;
        active = 0;
        for (const auto& p : book) if (p.qty) {
            gross += std::abs(p.qty * p.mark);
            upnl += p.qty * (p.mark - p.entry);
            ++active;
        }
    }

    void execute(const Request& r, Time stamp, double ref, unsigned char status,
                 double volume, double& gross, double& upnl, Time& active, Time system_reason = 0) {
        auto i = static_cast<std::size_t>(r.symbol);
        auto& p = book.at(i);
        Order log{};
        log.ms = log.bar_start = stamp;
        log.decision = r.decision; log.symbol = r.symbol; log.id = r.id; log.kind = r.kind;
        log.reference = ref; log.price = unset; log.target = unset; log.intent_size = r.size;
        log.after = p.qty; log.entry = p.entry;
        log.requested = (r.kind == 0 && price_ok(ref)) ? r.size / ref
                     : r.kind == 1 ? r.size : r.kind == 2 ? -p.qty
                     : r.kind == 3 ? -p.qty * r.size : unset;
        log.requested_notional = r.kind == 0 ? std::abs(r.size)
                               : price_ok(ref) ? std::abs(log.requested) * ref : unset;
        log.buying_power = std::max(0.0, (wallet + upnl) * cfg.leverage - gross);
        if (!system_reason) ++p.orders;
        auto reject = [&](Time reason) { log.reason = reason; orders.push_back(log); };
        if (status != 0) { reject(10 + status); return; }
        if (!price_ok(ref)) { reject(15); return; }
        if (!std::isfinite(log.requested) || !std::isfinite(log.requested_notional)
                || (r.kind == 3 && !(r.size > 0 && r.size <= 1))) { reject(16); return; }
        const double wanted = std::abs(log.requested), holding = std::abs(p.qty);
        const bool opposite = log.requested * p.qty < 0;
        // Exact closes are never trapped as dust. Reversals round only their newly opened leg.
        double amount = r.kind == 2 || (opposite && wanted == holding) ? holding : 0;
        if (!(amount > 0)) {
            const double close = opposite && wanted >= holding ? holding : 0;
            amount = close + std::floor((wanted - close) / order_step + 1e-9) * order_step;
        }
        double delta = std::copysign(amount, log.requested);
        log.rounded = delta;
        if (!std::isfinite(amount) || amount <= epsilon) { reject(16); return; }
        const double reduction = opposite ? std::min(holding, amount) : 0;
        double add = amount - reduction;
        const double other = std::max(0.0, gross - (holding ? holding * p.mark : 0));
        const double prior_upnl = p.qty ? p.qty * (p.mark - p.entry) : 0;
        const double at_open = upnl - prior_upnl + (p.qty ? p.qty * (ref - p.entry) : 0);
        log.buying_power = std::max(0.0, (wallet + at_open) * cfg.leverage - other - holding * ref);
        if (add > epsilon) {
            if (suspended || liquidation || wallet + at_open <= 0) { reject(18); return; }
            if ((wanted - (opposite ? holding : 0)) * ref + epsilon < cfg.min_adjustment) { reject(17); return; }
            if ((cfg.max_positions && !p.qty && active >= cfg.max_positions)
                 || (cfg.max_symbol && std::abs(p.qty + delta) * ref > cfg.max_symbol)
                 || (cfg.max_gross && other + std::abs(p.qty + delta) * ref > cfg.max_gross)
                 || (cfg.max_adjustment && amount * ref > cfg.max_adjustment)) { reject(19); return; }
        }
        // Nondefault capacity settings explicitly opt into observing bar volume.
        if (!system_reason && cfg.participation && (!price_ok(volume) || amount > volume * cfg.participation)) {
            reject(20); return;
        }
        const double impact = cfg.impact_bps && price_ok(volume) ? cfg.impact_bps * std::sqrt(amount / volume) : 0;
        const double fill = ref * (1 + std::copysign((cfg.spread_bps / 2 + cfg.slippage_bps + impact) / 10000, delta));
        if (!price_ok(fill)) { reject(15); return; }
        const double cost = amount * std::abs(fill - ref), fee = amount * fill * cfg.fee_bps / 10000;
        double leverage = cfg.max_gross_leverage ? std::min(cfg.leverage, cfg.max_gross_leverage) : cfg.leverage;
        if (add > epsilon && other + std::abs(p.qty + delta) * ref >
                std::max(0.0, wallet + at_open - cost - fee) * leverage + epsilon) { reject(18); return; }
        gross = other + holding * ref;
        upnl = at_open;
        p.mark = ref;
        log.reason = system_reason;
        book_fill(i, log, delta, fill, cost, fee, gross, upnl, active, false);
        if (system_reason == 21) --p.fills;
    }

    void snapshot(Time now, double gross, double upnl, Time uncertain) {
        double net = 0;
        for (const auto& p : book) if (p.qty) net += p.qty * p.mark;
        double equity = wallet + upnl, im = gross / cfg.leverage, mm = gross * cfg.maintenance_rate;
        peak = std::max(peak, equity);
        worst_dd = std::max(worst_dd, (peak - equity) / peak);
        current = {now, uncertain, static_cast<Time>(equity < mm), wallet, realized, upnl,
                   equity, gross, net, im, mm, equity - im, fees, slippage, funding, turnover, worst_dd};
    }

    void configure_shock(const ShockConfig& config) {
        if (cfg.latency != 0 || program) throw std::invalid_argument("Compiled policy requires zero latency and a fresh engine");
        program = std::make_unique<ShockProgram>(config, book.size());
    }

    void ordered(const Time* starts, const Market* values, const unsigned char* flags, const double* opens,
                 std::size_t ticks, const Settlement* events, std::size_t event_count,
                 const Request* requests, std::size_t request_count,
                 const double* const* columns = nullptr, const unsigned char* ready = nullptr,
                 Time start = 0, Time cadence = 1) {
        if (columns && (!program || !ready || cadence <= 0))
            throw std::invalid_argument("Missing compiled order program inputs");
        orders.clear(); cashflows.clear(); targets.clear(); curve.clear();
        const auto n = book.size();
        std::size_t f = 0, q = 0;
        for (std::size_t j = 0; j < ticks; ++j, ++count) {
            Time t = starts[j], end = t + cfg.interval;
            if (last >= 0 && t != last + cfg.interval) throw std::invalid_argument("Missing scheduled tick");
            last = t;
            const auto* market = values + j*n;
            const auto* state = flags + j*n;
            const auto* open = opens + j*n;
            while (f < event_count && events[f].ms <= t) settle(events[f++]);
            // Only observable opening trades can update the pre-order buying power.
            for (std::size_t i = 0; i < n; ++i)
                if (book[i].qty && state[i] == 0 && price_ok(open[i])) book[i].mark = open[i];
            double gross, upnl;
            Time active;
            totals(gross, upnl, active);
            if (wallet + upnl <= gross * cfg.maintenance_rate) liquidation = true;
            if (suspended || liquidation) for (std::size_t i = 0; i < n; ++i) {
                if (book[i].qty && state[i] == 0 && price_ok(open[i]))
                    execute({t, 0, static_cast<Time>(i), 2, t - cfg.interval, 0},
                            t, open[i], state[i], market[i].volume, gross, upnl, active, 22);
            }
            if (columns && !program_orders.empty()) {
                for (const auto& r : program_orders)
                    execute(r, t, open[r.symbol], state[r.symbol], market[r.symbol].volume, gross, upnl, active);
                program_orders.clear();
            }
            while (q < request_count && requests[q].due == t) {
                const auto& r = requests[q++];
                execute(r, t, open[r.symbol], state[r.symbol], market[r.symbol].volume, gross, upnl, active);
            }
            while (f < event_count && events[f].ms < end) settle(events[f++]);
            Time uncertain = 0;
            for (std::size_t i = 0; i < n; ++i) {
                auto& p = book[i];
                bool observed = price_ok(market[i].mark);
                if (observed) p.mark = market[i].mark;
                else if (cfg.trade_mark_fallback && state[i] == 0 && price_ok(market[i].close)) {
                    p.mark = market[i].close; observed = true; ++p.fallback_ticks;
                }
                p.gap_ticks += state[i] >= 2 && state[i] != 4;
                p.held_unmarked_ticks += p.qty && !observed;
                uncertain += p.qty && !observed;
            }
            totals(gross, upnl, active);
            double equity = wallet + upnl;
            peak = std::max(peak, equity);
            double dd = (peak - equity) / peak;
            worst_dd = std::max(worst_dd, dd);
            if (cfg.drawdown_limit > 0 && dd >= cfg.drawdown_limit) suspended = true;
            if (equity <= gross * cfg.maintenance_rate) liquidation = true;
            risk.peak_gross = std::max(risk.peak_gross, gross);
            risk.maintenance_ticks += equity < gross * cfg.maintenance_rate;
            risk.uncertain_ticks += uncertain > 0;
            risk.suspended_ticks += suspended || liquidation;
            bool sample = end % 86400000 == 0 || (cfg.logging == 1 && count % cfg.snapshot_ticks == 0);
            if (sample || j+1 == ticks) snapshot(end, gross, upnl, uncertain);
            if (sample) curve.push_back(current);
            if (columns && (t - start) / cfg.interval % cadence == 0) {
                program->decide({end, n, j, columns, state, ready + j*n,
                                 book, equity - gross / cfg.leverage}, program_orders);
            }
        }
        if (f != event_count || q != request_count) throw std::invalid_argument("Events cross the block boundary");
    }

    void terminal(Time end, const Request* outstanding, std::size_t size) {
        orders.clear(); cashflows.clear(); targets.clear(); curve.clear();
        for (std::size_t i = 0; i < size + program_orders.size(); ++i) {
            const auto& r = i < size ? outstanding[i] : program_orders[i - size];
            const auto& p = book.at(r.symbol);
            ++book[r.symbol].orders;
            Order log{};
            log.ms = log.bar_start = r.due; log.decision = r.decision; log.id = r.id;
            log.symbol = r.symbol; log.kind = r.kind; log.reason = 23; log.intent_size = r.size;
            log.after = p.qty; log.entry = p.entry; log.price = log.reference = log.target = unset;
            log.requested = r.kind == 1 ? r.size : r.kind == 2 ? -p.qty : r.kind == 3 ? -p.qty * r.size : unset;
            log.requested_notional = r.kind == 0 ? std::abs(r.size) : unset;
            log.buying_power = std::max(0.0, current.available * cfg.leverage);
            orders.push_back(log);
        }
        program_orders.clear();
        double gross, upnl;
        Time active, uncertain = current.uncertain_positions;
        totals(gross, upnl, active);
        for (std::size_t i = 0; i < book.size(); ++i) if (book[i].qty)
            execute({end, 0, static_cast<Time>(i), 2, end, 0}, end, book[i].mark, 0, 0,
                    gross, upnl, active, 21);
        snapshot(end, gross, upnl, uncertain);
        curve.push_back(current);
    }

    template<class Input>
    void run(const Time* starts, const Input* values, const unsigned char* status, std::size_t ticks,
             const Settlement* settlement, std::size_t funding_count, const double* requested_targets = nullptr) {
        orders.clear(); cashflows.clear(); targets.clear(); curve.clear();
        const auto n = book.size();
        std::size_t next_funding = 0;
        for (std::size_t j = 0; j < ticks; ++j, ++count) {
            Time start = starts[j], now = start + cfg.interval + cfg.lag;
            if (start <= last) throw std::invalid_argument("Out-of-order bars");
            last = start;
            const auto* row = values + j*n;
            const auto* flags = status + j*n;
            while (next_funding < funding_count && settlement[next_funding].ms <= now)
                settle(settlement[next_funding++]);
            double gross = 0, upnl = 0;
            Time active = 0, uncertain = 0;
            for (std::size_t i = 0; i < n; ++i) {
                auto& p = book[i];
                auto& b = row[i];
                fresh[i] = price_ok(b.mark);
                if (fresh[i]) p.mark = b.mark;
                else if (cfg.trade_mark_fallback && flags[i] == 0 && price_ok(b.close)) {
                    p.mark = b.close; fresh[i] = true; ++p.fallback_ticks;
                }
                p.gap_ticks += flags[i] >= 2 && flags[i] != 4;
                if (!p.qty) continue;
                ++active;
                p.held_unmarked_ticks += !fresh[i];
                uncertain += !fresh[i];
                gross += std::abs(p.qty * p.mark);
                upnl += p.qty * (p.mark - p.entry);
            }
            for (std::size_t i = 0; i < n; ++i) {
                auto& p = book[i];
                const auto& b = row[i];
                // Drawdown liquidation decisions cannot fill in the event that revealed the breach.
                if (p.pending && start >= p.decision + cfg.latency && flags[i] == 0
                        && price_ok(b.execution) && b.volume > 0 && price_ok(p.mark)
                        && (!suspended || p.decision < now && p.target == 0))
                    adjust(i, b, start, now, gross, upnl, active, uncertain);
            }
            const double equity = wallet + upnl;
            peak = std::max(peak, equity);
            const double dd = peak > 0 ? (peak - equity) / peak : 0;
            worst_dd = std::max(worst_dd, dd);
            if (cfg.drawdown_limit > 0 && dd >= cfg.drawdown_limit) suspended = true;
            risk.peak_gross = std::max(risk.peak_gross, gross);
            risk.maintenance_ticks += equity < gross * cfg.maintenance_rate;
            risk.uncertain_ticks += uncertain != 0;
            risk.suspended_ticks += suspended;
            const bool rebalance = count % cfg.rebalance_ticks == 0;
            if (rebalance || suspended) for (std::size_t i = 0; i < n; ++i) {
                auto& p = book[i];
                if (suspended) {
                    if (p.qty && (!p.pending || p.target != 0)) {
                        p.pending = true; p.target = 0; p.decision = now;
                        if (cfg.logging == 1) targets.push_back({now, static_cast<Time>(i), 0});
                    }
                    continue;
                }
                if (!rebalance || flags[i] >= 2) continue;
                double requested = unset;
                if (native) requested = native->target(i, std::isfinite(p.target));
                else if constexpr (std::is_same_v<Input, Bar>) requested = row[i].target;
                else if (requested_targets) requested = requested_targets[j*n+i];
                if (std::isfinite(requested) && (!p.pending || requested != p.target)) {
                    p.target = requested; p.decision = now; p.pending = true;
                    if (cfg.logging == 1) targets.push_back({now, static_cast<Time>(i), requested});
                }
            }
            const bool daily = (start + cfg.interval) % 86400000 == 0;
            const bool block_end = j + 1 == ticks;
            if ((cfg.logging != 2 && count % cfg.snapshot_ticks == 0) || daily || block_end) {
                double net = 0;
                for (const auto& p : book) if (p.qty) net += p.qty * p.mark;
                double im = gross / cfg.leverage, mm = gross * cfg.maintenance_rate;
                current = {now, uncertain, static_cast<Time>(wallet + upnl < mm), wallet, realized,
                           upnl, wallet + upnl, gross, net, im, mm, wallet + upnl - im,
                           fees, slippage, funding, turnover, worst_dd};
                if (cfg.logging) curve.push_back(current);
            }
        }
        if (next_funding != funding_count) throw std::invalid_argument("Funding events extend beyond this block");
    }
};

EXPORTED Engine* sim_create(const Config* c, const Rule* rules, std::size_t n, const double* native_target) {
    try { return new Engine(*c, rules, n, native_target); } catch (...) { return nullptr; }
}
EXPORTED void sim_delete(Engine* sim) { delete sim; }
EXPORTED int sim_run(Engine* sim, const Time* ts, const Bar* bars, const unsigned char* status, std::size_t n,
                     const Settlement* funding, std::size_t funding_count) {
    try { sim->run(ts, bars, status, n, funding, funding_count); return 0; } catch (...) { return -1; }
}
EXPORTED int sim_run_shared(Engine* sim, const Time* ts, const Market* bars, const unsigned char* status,
                            std::size_t n, const Settlement* funding, std::size_t funding_count,
                            const double* targets) {
    try { sim->run(ts, bars, status, n, funding, funding_count, targets); return 0; } catch (...) { return -1; }
}
EXPORTED void sim_risk(Engine* sim, RiskSummary* out) { *out = sim->risk_summary(); }
EXPORTED const Order* sim_orders(Engine* sim, std::size_t* n) { *n = sim->orders.size(); return sim->orders.data(); }
EXPORTED const Cashflow* sim_cashflows(Engine* sim, std::size_t* n) { *n = sim->cashflows.size(); return sim->cashflows.data(); }
EXPORTED const Target* sim_targets(Engine* sim, std::size_t* n) { *n = sim->targets.size(); return sim->targets.data(); }
EXPORTED const Equity* sim_curve(Engine* sim, std::size_t* n) { *n = sim->curve.size(); return sim->curve.data(); }
EXPORTED void sim_equity(Engine* sim, Equity* out) { *out = sim->final(); }
EXPORTED void sim_positions(Engine* sim, PositionView* out) { sim->positions(out); }

EXPORTED void sim_orders_config(Engine* sim, double step) { sim->orders_config(step); }
EXPORTED int sim_run_orders(Engine* sim, const Time* starts, const Market* market, const unsigned char* flags,
                            const double* opens, std::size_t ticks, const Settlement* funding,
                            std::size_t events, const Request* requests, std::size_t count) {
    try { sim->ordered(starts, market, flags, opens, ticks, funding, events, requests, count); return 0; }
    catch (...) { return -1; }
}
EXPORTED int sim_terminal(Engine* sim, Time end, const Request* requests, std::size_t count) {
    try { sim->terminal(end, requests, count); return 0; } catch (...) { return -1; }
}

EXPORTED int sim_configure_shock(Engine* sim, const ShockConfig* config) {
    try { sim->configure_shock(*config); return 0; } catch (...) { return -1; }
}
EXPORTED int sim_run_program(Engine* sim, const Time* starts, const Market* market, const unsigned char* flags,
                              const double* opens, std::size_t ticks, const Settlement* funding,
                              std::size_t events, const double* const* columns, const unsigned char* ready,
                              Time start, Time cadence) {
    try { sim->ordered(starts, market, flags, opens, ticks, funding, events, nullptr, 0,
                       columns, ready, start, cadence); return 0; } catch (...) { return -1; }
}

EXPORTED ShockFeatures* sim_shock_features_create(std::size_t n, int flow, int range) {
    try { return new ShockFeatures(n, flow != 0, range != 0); } catch (...) { return nullptr; }
}
EXPORTED void sim_shock_features_delete(ShockFeatures* self) { delete self; }
EXPORTED int sim_shock_features_run(ShockFeatures* self, const double* close, const double* quote,
                                     const double* buy, const double* sell, const unsigned char* status,
                                     std::size_t rows, double* out) {
    try { self->evaluate(close, quote, buy, sell, status, rows, out); return 0; } catch (...) { return -1; }
}
