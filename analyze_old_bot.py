import csv
from datetime import datetime
from collections import defaultdict

files = [
    '/Users/bradamanka/Downloads/GAANG-main/oldbadbot.csv',
    '/Users/bradamanka/Downloads/ORIGINAL-PolyBotTOP copy/our-old-bot_full_activity.csv',
]

for filepath in files:
    try:
        rows = []
        with open(filepath, encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append(r)
    except Exception as e:
        print(f"SKIP {filepath}: {e}")
        continue

    print(f"\n{'='*70}")
    print(f"FILE: {filepath.split('/')[-1]}")
    print(f"Total transactions: {len(rows)}")
    print(f"{'='*70}")

    # Action breakdown
    actions = defaultdict(int)
    action_usd = defaultdict(float)
    for r in rows:
        action = r['action']
        actions[action] += 1
        action_usd[action] += float(r['usdcAmount'] or 0)

    print('\n--- ACTION BREAKDOWN ---')
    for a in sorted(actions.keys()):
        print(f'  {a}: {actions[a]} txns | ${action_usd[a]:,.2f}')

    # Money flow
    deposits = sum(float(r['usdcAmount']) for r in rows if r['action'] == 'Deposit')
    buys = sum(float(r['usdcAmount']) for r in rows if r['action'] == 'Buy')
    redeems_val = [float(r['usdcAmount']) for r in rows if r['action'] == 'Redeem']
    redeems_total = sum(redeems_val)
    redeems_zero = sum(1 for v in redeems_val if v == 0)
    redeems_nonzero = sum(1 for v in redeems_val if v > 0)
    merges = sum(float(r['usdcAmount']) for r in rows if r['action'] == 'Merge')
    merge_count = sum(1 for r in rows if r['action'] == 'Merge')

    print('\n--- MONEY FLOW ---')
    print(f'  Deposited:     ${deposits:,.2f}')
    print(f'  Bought:        ${buys:,.2f} ({actions["Buy"]} orders)')
    print(f'  Merged:        ${merges:,.2f} ({merge_count} merges)')
    print(f'  Redeemed:      ${redeems_total:,.2f} ({actions["Redeem"]} redeems)')
    print(f'    $0 redeems:  {redeems_zero} (total loss)')
    print(f'    >0 redeems:  {redeems_nonzero} (won)')
    pnl = merges + redeems_total - buys
    print(f'  Net P&L:       ${pnl:,.2f}')
    print(f'  Final balance: ${deposits + pnl:,.2f}')
    if buys > 0:
        print(f'  ROI:           {pnl/deposits*100:+.1f}%')

    # Timeline
    timestamps = [int(r['timestamp']) for r in rows if r['timestamp']]
    if timestamps:
        start = datetime.fromtimestamp(min(timestamps))
        end = datetime.fromtimestamp(max(timestamps))
        print(f'\n--- TIMELINE ---')
        print(f'  Start: {start}')
        print(f'  End:   {end}')
        print(f'  Duration: {end - start}')

    # Market breakdown
    markets = defaultdict(lambda: {'buys': 0, 'buy_usd': 0, 'redeems': 0, 'redeem_usd': 0, 'merges': 0, 'merge_usd': 0})
    for r in rows:
        if r['action'] == 'Deposit':
            continue
        m = r['marketName']
        if r['action'] == 'Buy':
            markets[m]['buys'] += 1
            markets[m]['buy_usd'] += float(r['usdcAmount'])
        elif r['action'] == 'Redeem':
            markets[m]['redeems'] += 1
            markets[m]['redeem_usd'] += float(r['usdcAmount'])
        elif r['action'] == 'Merge':
            markets[m]['merges'] += 1
            markets[m]['merge_usd'] += float(r['usdcAmount'])

    print(f'\n--- MARKETS ({len(markets)} unique) ---')
    wins = 0
    losses = 0
    for m_name, m_data in sorted(markets.items(), key=lambda x: x[1]['buy_usd'], reverse=True):
        revenue = m_data['merge_usd'] + m_data['redeem_usd']
        market_pnl = revenue - m_data['buy_usd']
        if market_pnl >= 0:
            wins += 1
        else:
            losses += 1
        emoji = '+' if market_pnl >= 0 else 'X'
        print(f'  [{emoji}] {m_name[:55]}')
        print(f'      Cost: ${m_data["buy_usd"]:.2f} ({m_data["buys"]} buys) | Merge: ${m_data["merge_usd"]:.2f} | Redeem: ${m_data["redeem_usd"]:.2f} | PnL: ${market_pnl:+.2f}')
    
    print(f'\n  Market W/L: {wins} wins / {losses} losses ({wins/(wins+losses)*100:.0f}% win rate)')

    # Buy side breakdown
    buy_rows = [r for r in rows if r['action'] == 'Buy']
    up_buys = sum(float(r['usdcAmount']) for r in buy_rows if r.get('tokenName','').lower() in ('up','yes'))
    down_buys = sum(float(r['usdcAmount']) for r in buy_rows if r.get('tokenName','').lower() in ('down','no'))
    up_count = sum(1 for r in buy_rows if r.get('tokenName','').lower() in ('up','yes'))
    down_count = sum(1 for r in buy_rows if r.get('tokenName','').lower() in ('down','no'))
    total_buy_count = up_count + down_count
    if total_buy_count > 0:
        print(f'\n--- SIDE BREAKDOWN ---')
        print(f'  UP buys:   {up_count} orders | ${up_buys:.2f}')
        print(f'  DOWN buys: {down_count} orders | ${down_buys:.2f}')
        print(f'  Ratio:     {up_count/total_buy_count*100:.1f}% UP / {down_count/total_buy_count*100:.1f}% DOWN')

    # Merge efficiency
    if buys > 0:
        print(f'\n--- MERGE EFFICIENCY ---')
        merge_pct = merges / (merges + redeems_total) * 100 if (merges + redeems_total) > 0 else 0
        print(f'  Merge revenue %: {merge_pct:.1f}% (g22 was 97.7%)')
        print(f'  Capital turnover: {buys/deposits:.1f}x')
