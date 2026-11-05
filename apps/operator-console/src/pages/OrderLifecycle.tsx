import { lifecycle } from "./orders.fixtures";

export function OrderLifecycle() {
  return <div className="order-lifecycle-stages">
    {lifecycle.map(([number, label, state, detail, tone]) =>
      <article className="lifecycle-stage" key={number}>
        <span>{number}</span><b>{label}</b>
        <strong className={`order-tone-${tone}`}>{state}</strong>
        <small>{detail}</small>
      </article>)}
  </div>;
}
