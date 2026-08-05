/**
 * chart.js — Canvas-based line chart with colored limit zones.
 *
 * Draws a time series with:
 *   - Blue line for measurements within limits
 *   - Yellow line/zone for incumple_vp range
 *   - Red line/zone for no_apta range
 */

const Chart = {
    /**
     * Render a time-series chart into a canvas element.
     *
     * @param {HTMLCanvasElement} canvas
     * @param {Array} series — [[date_str, value], ...]
     * @param {Object} limits — from dict entry: {vp, vna, isRange}
     *   vp: number | [lo, hi]
     *   vna: number | [lo, hi] | null
     */
    render(canvas, series, limits) {
        const ctx = canvas.getContext("2d");
        const dpr = window.devicePixelRatio || 1;
        const W = canvas.clientWidth;
        const H = canvas.clientHeight;
        canvas.width = W * dpr;
        canvas.height = H * dpr;
        ctx.scale(dpr, dpr);

        const pad = { top: 16, right: 16, bottom: 32, left: 50 };
        const plotW = W - pad.left - pad.right;
        const plotH = H - pad.top - pad.bottom;

        if (series.length === 0 || plotW <= 0 || plotH <= 0) return;

        // Parse data
        const points = series
            .filter((d) => d[1] != null)
            .map((d) => ({ date: new Date(d[0]), value: d[1] }))
            .sort((a, b) => a.date - b.date);

        if (points.length === 0) return;

        // Compute scales
        const dates = points.map((p) => p.date.getTime());
        const values = points.map((p) => p.value);

        let minX = Math.min(...dates);
        let maxX = Math.max(...dates);
        if (minX === maxX) {
            minX -= 86400000;
            maxX += 86400000;
        }

        // Determine Y range including limit zones
        let allValues = [...values];
        const vpVals = this._limitValues(limits.vp);
        const vnaVals = limits.vna ? this._limitValues(limits.vna) : [];
        allValues.push(...vpVals, ...vnaVals);

        let minY = Math.min(...allValues);
        let maxY = Math.max(...allValues);
        const yRange = maxY - minY || 1;
        minY -= yRange * 0.1;
        maxY += yRange * 0.1;

        const scaleX = (t) => pad.left + ((t - minX) / (maxX - minX)) * plotW;
        const scaleY = (v) => pad.top + plotH - ((v - minY) / (maxY - minY)) * plotH;

        // Clear
        ctx.clearRect(0, 0, W, H);

        // Draw limit zones
        this._drawZones(ctx, limits, minY, maxY, scaleX, scaleY, minX, maxX, pad, plotW, plotH);

        // Draw limit lines
        this._drawLimitLines(ctx, limits, scaleX, scaleY, minX, maxX, pad, plotW);

        // Draw axes
        this._drawAxes(ctx, pad, plotW, plotH, minX, maxX, minY, maxY, scaleX, scaleY);

        // Draw data line with color changes
        this._drawLine(ctx, points, limits, scaleX, scaleY);

        // Draw data points
        this._drawPoints(ctx, points, limits, scaleX, scaleY);
    },

    _limitValues(lim) {
        if (lim == null) return [];
        if (Array.isArray(lim)) return lim;
        return [lim];
    },

    _isInRange(value, vp, vna) {
        // Returns: "ok", "warning", "danger"
        if (vp == null) return "ok";

        if (Array.isArray(vp)) {
            // Range param (e.g. pH)
            const inVp = value >= vp[0] && value <= vp[1];
            if (inVp) return "ok";
            if (vna && Array.isArray(vna)) {
                const inVna = value >= vna[0] && value <= vna[1];
                if (inVna) return "warning";
                return "danger";
            }
            return "warning";
        } else {
            // Normal param
            if (value <= vp) return "ok";
            if (vna != null && value <= vna) return "warning";
            if (vna != null) return "danger";
            return "danger"; // no VNA means exceeding VP = danger
        }
    },

    _drawZones(ctx, limits, minY, maxY, scaleX, scaleY, minX, maxX, pad, plotW, plotH) {
        const vp = limits.vp;
        const vna = limits.vna;
        if (vp == null) return;

        const left = pad.left;
        const right = pad.left + plotW;

        if (Array.isArray(vp)) {
            // Range param: green zone between vp[0] and vp[1]
            const y0 = Math.max(scaleY(vp[1]), pad.top);
            const y1 = Math.min(scaleY(vp[0]), pad.top + plotH);

            // Yellow zones: between vna and vp boundaries
            if (vna && Array.isArray(vna)) {
                // Yellow below green: vna[0] to vp[0]
                const wy0 = Math.min(scaleY(vna[0]), pad.top + plotH);
                const wy1 = Math.min(scaleY(vp[0]), pad.top + plotH);
                ctx.fillStyle = "rgba(255, 152, 0, 0.08)";
                ctx.fillRect(left, wy1, plotW, wy0 - wy1);

                // Yellow above green: vp[1] to vna[1]
                const wy2 = Math.max(scaleY(vna[1]), pad.top);
                const wy3 = Math.max(scaleY(vp[1]), pad.top);
                ctx.fillRect(left, wy2, plotW, wy3 - wy2);

                // Red zones: beyond vna
                ctx.fillStyle = "rgba(244, 67, 54, 0.08)";
                const ry0 = Math.max(scaleY(maxY), pad.top);
                ctx.fillRect(left, ry0, plotW, wy2 - ry0); // above vna[1]
                const ry1 = Math.min(scaleY(minY), pad.top + plotH);
                ctx.fillRect(left, wy0, plotW, ry1 - wy0); // below vna[0]
            }
        } else {
            // Normal param: green below vp
            const vpY = scaleY(vp);

            if (vna != null) {
                // Yellow: between vp and vna
                const vnaY = scaleY(vna);
                ctx.fillStyle = "rgba(255, 152, 0, 0.08)";
                ctx.fillRect(left, vnaY, plotW, vpY - vnaY);

                // Red: above vna
                ctx.fillStyle = "rgba(244, 67, 54, 0.08)";
                ctx.fillRect(left, pad.top, plotW, vnaY - pad.top);
            } else {
                // Red: above vp (no VNA)
                ctx.fillStyle = "rgba(244, 67, 54, 0.08)";
                ctx.fillRect(left, pad.top, plotW, vpY - pad.top);
            }
        }
    },

    _drawLimitLines(ctx, limits, scaleX, scaleY, minX, maxX, pad, plotW) {
        const vp = limits.vp;
        const vna = limits.vna;
        if (vp == null) return;

        const left = pad.left;
        const right = pad.left + plotW;

        ctx.setLineDash([4, 4]);

        const drawHLine = (y, color) => {
            const sy = scaleY(y);
            ctx.strokeStyle = color;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(left, sy);
            ctx.lineTo(right, sy);
            ctx.stroke();
        };

        if (Array.isArray(vp)) {
            drawHLine(vp[0], "rgba(255, 152, 0, 0.5)");
            drawHLine(vp[1], "rgba(255, 152, 0, 0.5)");
            if (vna && Array.isArray(vna)) {
                drawHLine(vna[0], "rgba(244, 67, 54, 0.5)");
                drawHLine(vna[1], "rgba(244, 67, 54, 0.5)");
            }
        } else {
            drawHLine(vp, "rgba(255, 152, 0, 0.5)");
            if (vna != null) {
                drawHLine(vna, "rgba(244, 67, 54, 0.5)");
            }
        }

        ctx.setLineDash([]);
    },

    _drawAxes(ctx, pad, plotW, plotH, minX, maxX, minY, maxY, scaleX, scaleY) {
        ctx.strokeStyle = "#ddd";
        ctx.lineWidth = 1;

        // Y axis
        ctx.beginPath();
        ctx.moveTo(pad.left, pad.top);
        ctx.lineTo(pad.left, pad.top + plotH);
        ctx.stroke();

        // X axis
        ctx.beginPath();
        ctx.moveTo(pad.left, pad.top + plotH);
        ctx.lineTo(pad.left + plotW, pad.top + plotH);
        ctx.stroke();

        // Y labels
        ctx.fillStyle = "#999";
        ctx.font = "10px -apple-system, sans-serif";
        ctx.textAlign = "right";
        const ySteps = 5;
        for (let i = 0; i <= ySteps; i++) {
            const v = minY + (i / ySteps) * (maxY - minY);
            const y = scaleY(v);
            ctx.fillText(this._formatNum(v), pad.left - 4, y + 3);

            if (i > 0 && i < ySteps) {
                ctx.strokeStyle = "#f0f0f0";
                ctx.beginPath();
                ctx.moveTo(pad.left, y);
                ctx.lineTo(pad.left + plotW, y);
                ctx.stroke();
            }
        }

        // X labels (dates)
        ctx.textAlign = "center";
        ctx.fillStyle = "#999";
        const xRange = maxX - minX;
        const maxLabels = Math.min(6, Math.floor(plotW / 70));
        for (let i = 0; i <= maxLabels; i++) {
            const t = minX + (i / maxLabels) * xRange;
            const x = scaleX(t);
            const d = new Date(t);
            const label =
                d.getDate().toString().padStart(2, "0") +
                "/" +
                (d.getMonth() + 1).toString().padStart(2, "0") +
                "/" +
                (d.getFullYear() % 100);
            ctx.fillText(label, x, pad.top + plotH + 16);
        }
    },

    _statusColor(status) {
        if (status === "danger") return "#e53935";
        if (status === "warning") return "#fb8c00";
        return "#1976d2";
    },

    /**
     * Draw the series, splitting each segment wherever it crosses a limit.
     *
     * Colouring a whole segment by one of its endpoints makes the line
     * disagree with the background zones: a segment falling from a valid
     * value into the red band would stay blue all the way down. Instead we
     * cut the segment at every threshold it crosses and colour each piece by
     * the zone it actually lies in, so the colour changes exactly where the
     * line crosses the dashed limit.
     */
    _drawLine(ctx, points, limits, scaleX, scaleY) {
        if (points.length < 2) return;

        // Every value at which the status can change.
        const bounds = [
            ...this._limitValues(limits.vp),
            ...(limits.vna != null ? this._limitValues(limits.vna) : []),
        ];

        ctx.lineWidth = 2;

        for (let i = 0; i < points.length - 1; i++) {
            const p1 = points[i];
            const p2 = points[i + 1];
            const t1 = p1.date.getTime();
            const t2 = p2.date.getTime();
            const v1 = p1.value;
            const v2 = p2.value;

            // Positions along the segment (0..1) where it crosses a limit.
            const cuts = [0, 1];
            if (v2 !== v1) {
                for (const b of bounds) {
                    const f = (b - v1) / (v2 - v1);
                    if (f > 0 && f < 1 && !cuts.includes(f)) cuts.push(f);
                }
            }
            cuts.sort((a, b) => a - b);

            for (let k = 0; k < cuts.length - 1; k++) {
                const fa = cuts[k];
                const fb = cuts[k + 1];
                // The midpoint sits strictly inside one zone, so it decides
                // the colour without landing on a boundary.
                const mid = v1 + (v2 - v1) * ((fa + fb) / 2);

                ctx.strokeStyle = this._statusColor(
                    this._isInRange(mid, limits.vp, limits.vna)
                );
                ctx.beginPath();
                ctx.moveTo(
                    scaleX(t1 + (t2 - t1) * fa),
                    scaleY(v1 + (v2 - v1) * fa)
                );
                ctx.lineTo(
                    scaleX(t1 + (t2 - t1) * fb),
                    scaleY(v1 + (v2 - v1) * fb)
                );
                ctx.stroke();
            }
        }
    },

    _drawPoints(ctx, points, limits, scaleX, scaleY) {
        for (const p of points) {
            const status = this._isInRange(p.value, limits.vp, limits.vna);
            ctx.fillStyle = this._statusColor(status);
            ctx.beginPath();
            ctx.arc(
                scaleX(p.date.getTime()),
                scaleY(p.value),
                3,
                0,
                Math.PI * 2
            );
            ctx.fill();
        }
    },

    _formatNum(v) {
        if (Math.abs(v) >= 1000) return v.toFixed(0);
        if (Math.abs(v) >= 1) return v.toFixed(1);
        return v.toFixed(2);
    },
};
