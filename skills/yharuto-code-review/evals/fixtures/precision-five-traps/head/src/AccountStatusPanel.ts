export interface ReactHooksRuntime {
  useState<T>(initial: T): [T, (next: T) => void];
  useEffect(effect: () => void | (() => void), deps: readonly unknown[]): void;
}

export interface AccountStatusBus {
  subscribe(accountId: string, onChange: (online: boolean) => void): () => void;
}

export interface AccountStatusPanelProps {
  accountId: string;
  accountSummary: { displayName: string; planName: string } | null;
  statusBus: AccountStatusBus;
}

export interface VirtualNode {
  tag: string;
  props: Record<string, string>;
  text: string;
}

export function AccountStatusPanel(
  props: AccountStatusPanelProps,
  hooks: ReactHooksRuntime,
): VirtualNode | null {
  const [online, setOnline] = hooks.useState<boolean>(false);

  hooks.useEffect(() => {
    const unsubscribe = props.statusBus.subscribe(props.accountId, (isOnline) => {
      setOnline(isOnline);
    });
    return () => {
      unsubscribe();
    };
  }, [props.accountId, props.statusBus]);

  if (props.accountSummary === null) {
    return null;
  }

  return {
    tag: "section",
    props: {
      "data-account-id": props.accountId,
      "data-online": String(online),
    },
    text: `${props.accountSummary.displayName} (${props.accountSummary.planName})`,
  };
}
