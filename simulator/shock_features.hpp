#pragma once

#include <array>
#include <deque>

// Incremental causal graph for the fixed, versioned shock research policies.
// Price, flow, and prior-window state are shared across the complete wave.
class ShockFeatures {
    template<std::size_t N> struct Window {
        std::array<double,N> ring;
        double sum = 0, squares = 0;
        std::size_t valid = 0;
        Window() { ring.fill(unset); }
        void update(std::size_t t, double value, bool variance = false) {
            double old = ring[t % N];
            if (std::isfinite(old)) {
                sum -= old;
                if (variance) squares -= old * old;
                --valid;
            }
            if (std::isfinite(value)) {
                sum += value;
                if (variance) squares += value * value;
                ++valid;
            }
            if (!valid) sum = squares = 0;
            ring[t % N] = value;
        }
        double mean(std::size_t minimum = N) const { return valid >= minimum ? sum / valid : unset; }
        double stddev() const {
            return valid == N ? std::sqrt(std::max(0., (squares - sum * sum / N) / (N-1))) : unset;
        }
    };
    struct SymbolState {
        std::array<double,361> price;
        std::array<std::array<double,8>,12> lag;
        std::deque<std::pair<std::size_t,double>> high, low;
        Window<120> vol, prior_volume;
        Window<12> aggr, curr_volume;
        double ema = unset;
        std::size_t run = 0;
        SymbolState() {
            price.fill(unset);
            for (auto& row : lag) row.fill(unset);
        }
    };
    std::vector<SymbolState> state;
    bool flow, range;
public:
    ShockFeatures(std::size_t n, bool f, bool r) : state(n), flow(f), range(r) {}
    // Output planes are fast, move, old fast, prior vol, trend, fair, aggression,
    // current volume, prior volume, prior range high, prior range low.
    void evaluate(const double* close, const double* quote, const double* buy, const double* sell,
                  const unsigned char* status, std::size_t rows, double* out, unsigned char* ready,
                  std::size_t warmup, std::size_t position, std::size_t first, std::size_t last) {
        const auto n = state.size(), plane = n * rows;
        constexpr double alpha = 2./241, weight = 1-alpha;
        for (std::size_t row = 0; row < rows; ++row) {
            auto t = position + row;
            auto ix = row*n;
            auto k360 = t % 361, k12 = t % 12;
            auto k12old = (k360 + 361 - 12) % 361;
            auto k60old = (k360 + 361 - 60) % 361;
            auto k360old = (k360 + 1) % 361;
            for (std::size_t i = first; i < last; ++i) {
                auto& s = state[i];
                auto offset = ix+i;
                double price = close[offset];
                s.run = std::isfinite(price) ? s.run+1 : 0;
                auto ret = [&](std::size_t period, std::size_t index) {
                    if (s.run <= period) return unset;
                    double old = s.price[index];
                    return old ? price / old - 1 : unset;
                };
                double fast = ret(12, k12old), move = ret(60, k60old), trend = ret(360, k360old);
                s.price[k360] = price;
                s.ema = std::isfinite(price) && std::isfinite(s.ema) ? alpha*price + weight*s.ema : price;
                s.vol.update(t, fast, true);
                auto& back = s.lag[k12];
                double a = unset, v = unset, vprior = unset;
                if (flow) {
                    double total = buy[offset] + sell[offset];
                    double signed_flow = std::isfinite(total) && total != 0
                                         ? (buy[offset] - sell[offset]) / total : unset;
                    s.aggr.update(t, signed_flow);
                    s.curr_volume.update(t, quote[offset]);
                    s.prior_volume.update(t, quote[offset]);
                    a = s.aggr.mean(6); v = s.curr_volume.mean();
                    vprior = s.prior_volume.mean();
                }
                double hi = unset, lo = unset;
                if (range) {
                    auto advance = [&](auto& deque, auto preferred) {
                        while (!deque.empty() && deque.front().first+180 <= t) deque.pop_front();
                        while (!deque.empty() && preferred(price, deque.back().second)) deque.pop_back();
                        if (std::isfinite(price)) deque.emplace_back(t, price);
                        return s.run >= 180 ? deque.front().second : unset;
                    };
                    hi = advance(s.high, [](double a, double b) { return a >= b; });
                    lo = advance(s.low, [](double a, double b) { return a <= b; });
                }
                bool valid = status[offset] <= 1;
                ready[offset] = valid && s.run && s.run >= warmup;
                out[offset] = valid ? fast : unset;
                out[plane+offset] = valid ? move : unset;
                out[2*plane+offset] = valid ? back[0] : unset;
                out[3*plane+offset] = valid ? back[1] : unset;
                out[4*plane+offset] = valid ? trend : unset;
                out[5*plane+offset] = valid ? back[2] : unset;
                if (flow) {
                    out[6*plane+offset] = valid ? a : unset;
                    out[7*plane+offset] = valid ? v : unset;
                    out[8*plane+offset] = valid ? back[3] : unset;
                }
                if (range) {
                    out[9*plane+offset] = valid ? back[4] : unset;
                    out[10*plane+offset] = valid ? back[5] : unset;
                }
                back = {fast, s.vol.stddev(), s.ema, vprior, hi, lo, unset, unset};
            }
        }
    }
};
