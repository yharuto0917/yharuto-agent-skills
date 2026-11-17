import { formatCurrencyJpy } from "./shared/currencyFormatter.ts";

export interface OrderPricingSummary {
  orderId: string;
  subtotalJpy: number;
  discountJpy: number;
  settled: boolean;
  campaignCode?: string;
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

export function syncCheckoutDiscountBadge(
  containerEl: CheckoutBadgeDomElement,
  badgeEl: CheckoutBadgeDomElement,
  summary: OrderPricingSummary,
): { formattedPayable: string; isDiscountActive: boolean } {
  const payableJpy = Math.max(0, summary.subtotalJpy - summary.discountJpy);
  const isDiscountActive = summary.discountJpy > 0;
  const formattedPayable = formatCurrencyJpy(payableJpy, summary.campaignCode);

  badgeEl.textContent = formattedPayable;
  badgeEl.dataset.discountActive = String(isDiscountActive);

  if (typeof badgeEl.classList?.toggle === "function") {
    badgeEl.classList.toggle("is-discounted", isDiscountActive);
  }
  if (typeof badgeEl.classList?.contains === "function") {
    badgeEl.dataset.wasHighlighted = String(badgeEl.classList.contains("is-discounted"));
  }
  if (isDiscountActive) {
    if (typeof badgeEl.setAttribute === "function") {
      badgeEl.setAttribute("aria-label", `割引適用後金額 ${formattedPayable}`);
    }
  } else if (typeof badgeEl.removeAttribute === "function") {
    badgeEl.removeAttribute("aria-label");
  }

  const labelEl =
    typeof containerEl.querySelector === "function"
      ? containerEl.querySelector("[data-role='campaign-note']")
      : null;

  if (labelEl) {
    if (isDiscountActive && typeof labelEl.classList?.add === "function") {
      labelEl.classList.add("is-visible");
    } else if (!isDiscountActive && typeof labelEl.classList?.remove === "function") {
      labelEl.classList.remove("is-visible");
    }
  } else if (isDiscountActive && typeof containerEl.appendChild === "function") {
    containerEl.appendChild({ textContent: formattedPayable, dataset: { role: "campaign-note" } });
  }

  return { formattedPayable, isDiscountActive };
}
