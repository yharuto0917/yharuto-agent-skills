import type { CheckoutBadgeDomElement } from "../../src/checkoutBadge.ts";

export function createBareStubElement(): CheckoutBadgeDomElement {
  return {
    textContent: "",
    dataset: {},
  };
}
