# Python 監査基準

Python（FastAPI、Django、データ処理、CLI、非同期ワーカー等）の変更時に読み込む基準である。イテレータの1パス枯渇、ミュータブル既定引数、`async def` 内のブロッキング呼び出し、`CancelledError` を巻き込む広域例外捕捉、型・シリアライズ境界の落とし穴を確認する。

## 1. 1回消費イテレータ・ジェネレータの再走査枯渇

1. 関数の引数が `Iterable`、`Iterator`、`Generator`（ジェネレータ式、`map` / `filter` の戻り値、ファイルオブジェクト、DB ストリームカーソル等）を取りうる場合、同じ変数を2回以上走査していないかを精読する。1回目の `sum()`、`any()`、`list()`、内包表記で要素が消費し尽くされ、2回目のループが空になって `0` や空リストを返す不具合（`🔴 Bug`）を検出する。
2. 1回の `for` ループ内で複数の集計変数（件数・最大値・辞書など）を同時更新している処理に対し、複数の内包表記や `sum()` / `any()` への分割を提案してはならない。入力がイテレータだった場合に2パス目以降が枯渇するうえ、走査回数も増えるため単一ループ維持が正当である。
3. 長さが一致すべき複数のシーケンスを `zip()` で束ねる箇所で、要素数不一致時の末尾切り捨てが無視されてデータ欠落を生まないか（Python 3.10 以降での `zip(..., strict=True)` の要否）を確認する。

## 2. ミュータブル既定引数・クラス属性共有・クロージャ遅延束縛

1. 関数やメソッドの既定引数に `[]`、`{}`、`set()`、または可変インスタンスを直接指定し、呼び出しをまたいで要素が蓄積・汚染される不具合（`🔴 Bug`）がないかを確認する。既定値は `None` にして関数先頭で初期化するか、`dataclass` では `field(default_factory=...)` を使っているかを見る。
2. クラス定義直下に置かれたリストや辞書が全インスタンスで共有されるクラス変数となり、あるインスタンスへの変更が他インスタンスへ漏れないかを確認する。
3. ループ内で定義した `lambda` や内部関数がループ変数をそのまま参照し、実行時にすべて最終反復の値を見てしまう遅延束縛バグがないかを確認する。

## 3. async def 内の同期ブロッキングI/Oとタスク寿命

1. `async def` 関数およびその呼び出し先で、イベントループ全体を止める同期ブロッキング処理（`requests` 通信、`time.sleep`、同期 DB クライアント、`subprocess.run`、大きな同期ファイル読み書き、重い CPU 処理）を直接呼んでいないかを確認する。非同期クライアント、`asyncio.sleep`、`asyncio.to_thread` / `run_in_executor` へ置き換える必要がある箇所を `🔴 Bug` または `🟡 Risk` として報告する。
2. `asyncio.create_task()` で起動したタスクの参照を変数や集合に保持せず、ガベージコレクションによる途中消滅や未回収例外の放置が起きないかを確認する（`asyncio.TaskGroup` 等の構造化並行性の利用を含む）。
3. 直列 `for item in items: await call(item)` はレート制限や順序保証のための正当な実装であることが多いため、根拠なく `asyncio.gather` への一括並列化を提案しない。

## 4. 広域例外捕捉による CancelledError 消失とリソース管理

1. 素の `except:` や `except BaseException:` は `asyncio.CancelledError`、`KeyboardInterrupt`、`SystemExit` まで捕捉してタスクのタイムアウト中断やプロセス終了を妨げるため `🔴 Bug` または重大な `🟡 Risk` とする。旧互換経路やラッパー内で `except Exception:` がキャンセル例外やシステム異常を破棄していないかも確認する。
2. 広範囲を `try` で囲んで `except Exception: return None`（または `pass`、`[]`）とし、クエリ失敗や属性エラーを「データなし」に偽装していないかを確認する。例外捕捉は失敗しうる外部 I/O の1〜3行と具体的な例外型に限定し、再送出時は `raise ServiceError(...) from err` で原因連鎖を保っているかを見る。
3. ファイル、ソケット、HTTP クライアント、DB セッション・トランザクション、ロックが `with` / `async with` によって異常系でも確実に解放されるかを確認する。

## 5. Pydantic・型境界・日時と金額計算

- Pydantic の v1 記法と v2 記法の混在（`@validator` と `@field_validator`、`.dict()` と `.model_dump()`、`Config.orm_mode` と `model_config = ConfigDict(from_attributes=True)`）や、ミュータブル変更時の検証スキップがないかを確認する。
- 根拠のない `# type: ignore` や `cast(Any, ...)` による外部入力検証の素通りがないかを確認する。
- タイムゾーン情報を持たない naive `datetime`（`datetime.now()` / `datetime.utcnow()`）と aware `datetime`（`datetime.now(timezone.utc)`）の混在による比較時 `TypeError` や時刻ズレ、および金額・送料換算への `float` 使用（`decimal.Decimal` 未使用による丸め誤差）を確認する。
