import { OrderLifecycle } from "./OrderLifecycle";
import { evidenceRows, orderMetrics, orders, selectedFields } from "./orders.fixtures";
import "./orders.css";

const columns = ["Time","Order ID","Symbol","Side","Qty","Filled","Order Type","OMS Status","Broker Status","Strategy"];
const rowValues = (order: typeof orders[number]) => [order.time,order.id,order.symbol,order.side,order.quantity,order.filled,order.type,order.oms,order.broker,order.strategy];
const tone = (value:string) => value === "BUY" || value === "FILLED" || value === "ACKNOWLEDGED" ? "positive" : value === "SELL" || value === "REJECTED" ? "negative" : value === "PENDING" || value === "ACCEPTED" || value === "PARTIALLY_FILLED" ? "warning" : "muted";

export function Orders() {
  return <section className="orders-page" data-page="orders" data-classification="PAPER / REPLACEABLE DESIGN DATA">
    <style>{`.order-row{margin-bottom:4px}.evidence-row{height:26px;margin-bottom:2px}.orders-overview h1{left:15px;top:1px}.orders-overview>p{left:15px;top:25px}.orders-overview>span{top:4px}.order-metrics{left:15px;top:53px}.order-register h2{left:15px;top:13px}.order-register>p{top:13px}.chain-control{left:138px;top:10px}.table-control{left:504px;top:9px}.order-table{left:15px;top:47px}.selected-order h2{left:15px;top:13px}.selected-order>p{left:15px;top:34px}.selected-details{left:15px;top:55px}.selected-order>small{left:15px;top:391px}.order-lifecycle h2{left:15px;top:9px}.order-lifecycle>p{top:11px}.order-lifecycle-stages{left:15px;top:37px}.execution-summary h2{left:15px;top:11px}.execution-summary>button{left:180px;top:9px}.execution-summary>strong{top:13px}.execution-details{left:15px;top:41px}.order-exceptions h2{left:15px;top:11px}.order-exceptions>p{top:14px}.exception{left:15px}.exception.rejected{top:39px}.exception.cancelled{top:109px}.order-exceptions>em{left:15px;top:183px}.order-evidence h2{left:15px;top:11px}.order-evidence>p{top:14px}.evidence-list{left:15px;top:41px}.order-evidence>em{left:15px;top:187px}.order-tone-pass,.order-tone-acknowledged{color:#00d19e!important}.order-tone-created{color:#00d4ff!important}.order-tone-partial{color:#ffb81f!important}.order-tone-remaining{color:#a8bac9!important}`}</style>
    <div className="sr-only" aria-hidden="true"><h1>Orders &amp; OMS</h1><span>OMS-P-0008</span></div>
    <section className="orders-overview order-panel">
      <h1>Orders</h1><p>Order lifecycle, status, execution outcomes and evidence</p><span>Paper environment</span>
      <div className="order-metrics">{orderMetrics.map(([label,value,colour])=><article key={label}><small>{label}</small><b className={`metric-${colour}`}>{value}</b></article>)}</div>
    </section>
    <section className="order-register order-panel">
      <h2>Order Register</h2><button className="chain-control" type="button" aria-disabled="true">CHAIN ↗</button>
      <button className="table-control" type="button" aria-disabled="true">TABLE OPTIONS · NONINTERACTIVE ↗</button>
      <p>6 Paper orders · quantities<br/>reconcile by lifecycle</p>
      <div className="order-table" role="table" aria-label="Paper order register">
        <div className="order-head" role="row">{columns.map(column=><span role="columnheader" key={column}>{column}</span>)}</div>
        {orders.map(order=><div className={`order-row${order.id === "OMS-P-0003" ? " selected" : ""}`} role="row" key={order.id}>{rowValues(order).map((value,index)=><span role="cell" className={index===3||index===7||index===8?`order-tone-${tone(value)}`:""} key={`${order.id}-${index}`}>{value}</span>)}</div>)}
      </div>
    </section>
    <section className="selected-order order-panel">
      <h2>Selected Order</h2><p>AAPL · BUY · Paper</p>
      <div className="selected-details">{selectedFields.map(([label,value],index)=><div className={`field field-${index}`} key={label}><span>{label}</span><b className={value === "BUY" || value === "ACKNOWLEDGED" ? "order-tone-positive" : value === "PARTIALLY_FILLED" ? "order-tone-warning" : ""}>{value}</b></div>)}</div>
      <small>Risk Gateway: PASS · does not grant live authority</small>
    </section>
    <section className="order-lifecycle order-panel">
      <h2>Order Lifecycle</h2><p>OMS-P-0003 · AAPL BUY · PAPER</p><OrderLifecycle/>
    </section>
    <section className="execution-summary order-panel">
      <h2>Execution Summary</h2><button type="button" aria-disabled="true">COSTS ↗</button><strong>PARTIALLY_FILLED</strong>
      <div className="execution-details">
        <div className="quantity-card"><span>Ordered</span><b>120 shares</b></div><div className="quantity-card"><span>Filled</span><b className="order-tone-positive">45 shares</b></div><div className="quantity-card"><span>Remaining</span><b className="order-tone-warning">75 shares</b></div>
        <dl><dt>Order Limit</dt><dd>$190.00</dd><dt>Fill Count</dt><dd>1</dd><dt>Average Fill</dt><dd className="order-tone-positive">$189.82</dd><dt>Latest Fill</dt><dd>15:28:00 BST</dd><dt>Fill Evidence</dt><dd>EV-P-0003</dd><dt>Environment</dt><dd>PAPER · SYNTHETIC</dd></dl>
      </div>
    </section>
    <section className="order-exceptions order-panel">
      <h2>Order Exceptions</h2><p>2 recorded · canonical sample</p>
      <article className="exception rejected"><strong>BROKER REJECTED</strong><b>OMS-P-0005 · AMD BUY</b><span>0 / 60 filled · 60 unexecuted</span><small>Risk Gateway PASS · rejection is broker-side</small></article>
      <article className="exception cancelled"><strong>CANCELLED</strong><b>OMS-P-0006 · META SELL</b><span>20 / 90 filled · 70 terminated</span><small>Historical fill retained · no open quantity</small></article>
      <em>FAIL CLOSED · no additional order recorded</em>
    </section>
    <section className="order-evidence order-panel">
      <h2>Order Evidence</h2><p>OMS-P-0003 · PAPER</p>
      <div className="evidence-list">{evidenceRows.map(([label,id,value,toneName])=><div className="evidence-row" key={label}><span>{label}</span><b>{id}</b><strong className={`order-tone-${toneName}`}>{value}</strong></div>)}</div>
      <em>Evidence recorded ≠ independently verified execution</em>
    </section>
    <p className="sr-only">PAPER / REPLACEABLE DESIGN DATA · NO LIVE TRADING AUTHORITY · NO ORDER SUBMISSION · NO RECONCILIATION STATE ESTABLISHED</p>
  </section>;
}
