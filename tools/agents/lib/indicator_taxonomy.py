from __future__ import annotations


def classify_indicator_logic(logic: str) -> dict:
    l=(logic or '').lower()
    family='other'
    role='general_context'
    indicators=[]
    if l.startswith(('technical_ma','us_relative_strength','us_momentum','relative_strength_persistence','pullback_uptrend')) or 'momentum_breakout' in l:
        family='trend'
        role='trend_direction_filter'
        indicators.append('MA/relative_strength')
    if 'macd' in l:
        family='trend'
        role='trend_momentum_confirmation'
        indicators.append('MACD')
    if 'adx' in l:
        family='trend'
        role='trend_strength_filter'
        indicators.append('ADX')
    if l.startswith('technical_rsi') or '_rsi' in l or 'stochastic' in l or 'williams' in l or 'mfi' in l:
        family='momentum'
        role='entry_timing_or_reversion'
        if l.startswith('technical_rsi') or '_rsi' in l: indicators.append('RSI')
        if 'stochastic' in l: indicators.append('Stochastic')
        if 'williams' in l: indicators.append('Williams %R')
        if 'mfi' in l: indicators.append('MFI')
    if 'bollinger' in l or 'atr' in l or 'donchian' in l or 'keltner' in l or 'volatility' in l or 'range_grid' in l or 'range_' in l or 'contraction_breakout' in l:
        family='volatility'
        role='risk_sizing_or_breakout_band'
        if 'atr' in l: indicators.append('ATR')
        if 'donchian' in l: indicators.append('Donchian')
        if 'keltner' in l: indicators.append('Keltner')
        if 'bollinger' in l: indicators.append('Bollinger')
    if 'volume' in l or 'obv' in l or 'cmf' in l or 'vwap' in l or 'accumulation' in l:
        family='volume'
        role='volume_confirmation'
        if 'obv' in l: indicators.append('OBV')
        if 'cmf' in l: indicators.append('CMF')
        if 'vwap' in l: indicators.append('VWAP')
        if not indicators: indicators.append('Volume')
    if 'pivot' in l or 'fibonacci' in l or 'support' in l or 'resistance' in l or 'channel' in l:
        family='support_resistance'
        role='entry_exit_price_zone'
        if 'pivot' in l: indicators.append('Pivot')
        if 'fibonacci' in l: indicators.append('Fibonacci')
        if 'channel' in l: indicators.append('Channel')
    if l.startswith('technical_') and family == 'other':
        family='technical_other'
        role='supporting_alpha_context'
    is_technical = l.startswith('technical_') or family in {'trend','momentum','volatility','volume','support_resistance'}
    return {'indicator_family':family,'indicator_role':role,'indicator_components':indicators or [family],'is_technical_indicator':is_technical}
