#pragma once

// A trusted compiled policy consumes completed features and booked positions.
// Pending market requests always execute through Engine's existing execution path.
struct DecisionContext {
    Time now;
    std::size_t n, row;
    const double* const* columns;
    const unsigned char* status;
    const unsigned char* ready;
    const std::vector<Position>& book;
    double available;
};

class OrderProgram {
public:
    virtual ~OrderProgram() = default;
    virtual void decide(const DecisionContext&, std::vector<Request>&) = 0;
    Time next_id = 1;
};

enum ShockColumn { Close, Fast, Move, OldFast, PriorVol, Trend, Fair, Aggression, CurrentVolume, PriorVolume, RangeEdge };

struct ShockConfig {
    double depth, sigma, step, hold_minutes, order_usdt, inventory, stop, retrace;
    double market_limit, add_minutes, patient_minutes, minimum_gap;
    Time side, mechanism, dynamic, patient;
};

class ShockProgram final : public OrderProgram {
    struct Inventory {
        double extreme = unset, anchor = unset, last_add = unset, last_trim = unset;
        double prev_qty = 0, signal_price = unset;
        Time entered = 0, cooldown = 0, pending = 0;
        bool held = false;
    };
    ShockConfig p;
    std::vector<Inventory> state;
    std::vector<double> market_returns;
public:
    ShockProgram(const ShockConfig& params, std::size_t n) : p(params), state(n) {
        market_returns.reserve(n);
    }

    void decide(const DecisionContext& c, std::vector<Request>& out) override {
        const auto n = c.n;
        const auto* const* cols = c.columns;
        auto col = [&](ShockColumn k, std::size_t i) { return cols[k][c.row * n + i]; };
        double median = unset;
        if (p.mechanism == 1 || p.mechanism == 3) {
            market_returns.clear();
            for (std::size_t i = 0; i < n; ++i)
                if (c.status[i] == 0 && c.ready[i] && price_ok(col(Close, i))) market_returns.push_back(col(Move, i));
            if (market_returns.size() >= 12) {
                auto mid = market_returns.begin() + market_returns.size() / 2;
                std::nth_element(market_returns.begin(), mid, market_returns.end());
                median = *mid;
                if (market_returns.size() % 2 == 0)
                    median = (*std::max_element(market_returns.begin(), mid) + median) / 2;
            }
        }

        double margin = c.available;
        double side = static_cast<double>(p.side);
        double sqrt_five = std::sqrt(5.0);
        for (std::size_t i = 0; i < n; ++i) {
            auto& s = state[i];
            double spot = col(Close, i);
            double qty = side * c.book[i].qty;
            bool held = qty > 0;
            if (p.patient) {
                if (s.pending == 2 && qty > s.prev_qty) s.last_add = s.signal_price;
                if (s.pending == 3 && qty < s.prev_qty) s.last_trim = s.signal_price;
                s.prev_qty = qty;
                s.pending = 0;
            }
            if (s.held && !held) s.cooldown = c.now + (p.patient ? 30 : 15) * 60000;
            s.held = held;
            if (!held) s.extreme = s.last_trim = unset;
            if (c.status[i] != 0 || !price_ok(spot)) continue;
            bool new_extreme = held && side * spot < side * s.extreme;
            if (new_extreme) s.extreme = spot;

            double move = col(Move, i), fast = col(Fast, i), fair = col(Fair, i);
            double depth = -side * move;
            double vol = col(PriorVol, i);
            double fair_gap = side * (fair / spot - 1);
            double acceleration = -side * (fast - col(OldFast, i));
            bool shock = c.ready[i] && depth >= std::max(p.depth, p.sigma * vol * sqrt_five)
                         && -side * fast >= p.depth * .20;
            if (shock && p.mechanism == 0) {
                shock = acceleration > 0 && fair_gap > .0025;
            } else if (shock) {
                shock = fair_gap >= p.depth * .5;
                if (p.mechanism == 1 || p.mechanism == 3) {
                    double residual = -side * (move - median);
                    shock = shock && residual >= std::max(p.depth * .7, vol * sqrt_five * p.sigma)
                            && side * median > -p.market_limit && -side * col(Trend, i) < 3 * depth;
                }
                if (p.mechanism >= 2) {
                    double normal = col(PriorVolume, i);
                    shock = shock && -side * col(Aggression, i) >= .16
                            && normal > 0 && col(CurrentVolume, i) >= 1.6 * normal;
                    if (p.mechanism == 3) shock = shock && acceleration < 0 && fair_gap > p.depth * .75;
                }
            }
            if (p.patient)
                shock = shock && side * (col(RangeEdge, i) - spot) / spot >= p.depth
                        && side * (fair - spot) / spot >= p.minimum_gap;
            if (!held && (!shock || c.now < s.cooldown)) continue;

            auto send = [&](Time kind, double size) {
                out.push_back({c.now, next_id++, static_cast<Time>(i), kind, c.now, size});
            };
            double marked = qty * spot;
            if (held) {
                double ratio = c.now - s.entered;
                double entry = c.book[i].entry;
                double from_entry = side * (spot - entry) / entry;
                double bounce = side * (spot - s.extreme) / s.extreme;
                bool trimmed = std::isfinite(s.last_trim);
                bool more = !trimmed || side * (spot - s.last_trim) / spot >= p.step;
                double distance = side * (s.last_add - spot) / spot;
                if (!p.patient) {
                    double target = std::isfinite(fair) ? side * std::min(side * s.anchor, side * fair) : s.anchor;
                    double excursion = std::max(.001, side * (s.anchor - s.extreme) / s.anchor);
                    if (ratio >= p.hold_minutes * 60000 ||
                        side * (s.anchor - spot) / s.anchor > p.stop * p.depth ||
                        (side * (target - spot) / spot <= .0007 && bounce >= .002) ||
                        (trimmed && side * (s.last_trim - spot) / spot >= std::max(.003, .4 * excursion))) {
                        send(2, 0); continue;
                    }
                    double floor = p.retrace * excursion + (from_entry < 0 ? .0005 : 0);
                    if (more && bounce >= std::max(.0018, floor)) {
                        if (marked <= 2 * p.order_usdt) send(2, 0);
                        else send(0, -side * p.order_usdt);
                        s.last_trim = spot;
                        continue;
                    }
                    if (trimmed || !(new_extreme && distance >= p.step && shock)) continue;
                } else {
                    double elapsed = ratio / 60000;
                    double width = std::max(p.minimum_gap, side * (s.anchor - s.extreme) / s.extreme);
                    double remaining = side * (s.anchor - spot) / spot;
                    if (remaining > p.stop * p.depth || elapsed >= p.hold_minutes ||
                        (remaining <= .001 && bounce >= p.minimum_gap * .6) ||
                        (trimmed && side * (s.last_trim - spot) / spot >= std::max(.003, width * .40))) {
                        send(2, 0); continue;
                    }
                    bool enough = bounce >= std::max(.0035, p.retrace * width) + (from_entry < 0 ? .0005 : 0);
                    if (elapsed >= p.patient_minutes && enough && more) {
                        send(3, std::min(1., p.order_usdt / marked));
                        s.pending = 3; s.signal_price = spot;
                        continue;
                    }
                    double severity = std::max(1., std::min(2.5, depth / p.depth));
                    if (trimmed || remaining >= 1.9 * p.depth ||
                        !(shock && new_extreme && distance >= p.step / severity && elapsed <= p.add_minutes)) continue;
                }
            }
            double size = p.order_usdt;
            if (p.dynamic) size *= std::min(p.patient ? 2. : 2.5, std::max(1., depth / p.depth));
            if (marked + size > p.inventory || margin < size * 1.01) continue;
            send(0, side * size);
            margin -= size * 1.01;
            if (!held) {
                s.entered = c.now; s.anchor = fair; s.extreme = spot; s.last_add = spot;
            } else if (p.patient) {
                s.pending = 2; s.signal_price = spot;
            } else s.last_add = spot;
        }
    }
};
