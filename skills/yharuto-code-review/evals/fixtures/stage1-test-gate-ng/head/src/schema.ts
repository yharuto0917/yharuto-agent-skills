/**
 * Orders テーブルの永続化レコード型（全額日本円・最小整数円単位で格納）。
 */
export interface OrderRecord {
  id: string;
  customerId: string;
  /** 注文確定金額（日本円の整数円単位、例: 12000） */
  amountMinorJpy: number;
  /** 累積返金済み金額（日本円の整数円単位、初期値 0） */
  refundedMinorJpy: number;
  /** 通貨コード（現行DBスキーマでは "JPY" 固定） */
  currencyCode: "JPY";
  /** 決済ステータス */
  status: "captured" | "refunded" | "cancelled";
}

export interface RefundAllocation {
  orderId: string;
  approvedAmountJpy: number;
  remainingCapturableJpy: number;
  nextStatus: "captured" | "refunded";
}
