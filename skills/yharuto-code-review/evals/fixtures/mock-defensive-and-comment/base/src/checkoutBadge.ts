import { formatCurrencyJpy } from "./shared/currencyFormatter.ts";

export interface OrderPricingSummary {
  orderId: string;
  subtotalJpy: number;
  discountJpy: number;
  settled: boolean;
}

export interface CheckoutBadgeDomElement {
  textContent: string;
  dataset: Record<string, string | undefined>;
  classList?: {
    toggle?: (token: string, force?: boolean) => boolean;
    contains?: (token: string) => boolean;
    add?: (token: string) => void;
    remove?: (token: string) => void;
  };
  setAttribute?: (name: string, value: string) => void;
  removeAttribute?: (name: string) => void;
  querySelector?: (selector: string) => CheckoutBadgeDomElement | null;
  appendChild?: (child: CheckoutBadgeDomElement) => void;
}

export function renderCheckoutSummaryText(summary: OrderPricingSummary): string {
  const payable = Math.max(0, summary.subtotalJpy - summary.discountJpy);
  return formatCurrencyJpy(payable);
}
