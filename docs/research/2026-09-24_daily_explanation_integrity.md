# Daily explanation integrity / 2026-09-24

The morning prediction reports features that pushed its Ridge return estimate
up or down. That direction depends on both the fitted coefficient and the
day's standardized feature value. For example, a positive coefficient of 0.02
and a current standardized value of -1 contributes -0.02 to the return
forecast: it is a downward driver despite its positive fitted weight.

The previous fallback used coefficient order when scaler statistics were
missing or explanation calculation failed. It could label that example as an
upward driver. The revised path leaves daily driver lists empty and records
`daily feature contributions unavailable` in the prediction warnings when it
cannot calculate genuine current-day contributions. Missing feature values,
missing scaler entries and invalid numerical statistics also fail this
explanation calculation. It does not change the forecast, probability or BUY
rule. Fitted coefficients remain available as separate model diagnostics; they
are not presented as this morning's drivers.

This change does not alter explanations already saved with older forecasts.
The applicable regression tests check the sign reversal and that a missing
scaler or current input cannot trigger coefficient-sign fallback.
