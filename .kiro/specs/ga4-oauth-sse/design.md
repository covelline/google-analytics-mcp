# 設計ドキュメント

## 概要

このドキュメントでは、Google Analytics 4 MCPサーバーを修正して、OAuth2認証とHTTP-SSE通信をサポートするための設計を詳述します。現在のサーバーはサービスアカウント認証を使用し、標準的なMCPプロトコルを使用していますが、新しい設計ではユーザーがOAuth2を通じて自分のGoogleアカウントで認証できるようにし、HTTP-SSEを使用してClaude DesktopなどのLLMクライアントと通信できるようにします。

既存の `ga4_mcp_server.py` を修正して利用し、認証方法をOAuth2に変更し、MCPサーバー起動時のコマンドを `mcp.run("stdio")` から `mcp.run("sse")` に変更することで実装します。

## アーキテクチャ

修正後のシステムは以下の主要コンポーネントで構成されます：

1. **OAuth2認証モジュール**：Googleの認証フローを処理し、トークンを管理します
2. **既存のMCPサーバー**：FastMCPライブラリを使用して、SSEモードで実行します
3. **GA4データアクセス**：既存のGA4 API呼び出しを使用します

システムの全体的なアーキテクチャは以下の図のようになります：

```mermaid
graph TD
    User[ユーザー] -->|1. 起動| Server[GA4 MCPサーバー]
    Server -->|2. OAuth URL提供| User
    User -->|3. ブラウザでアクセス| Google[Google OAuth]
    Google -->|4. 認証コード| Server
    Server -->|5. トークン交換| Google
    LLM[Claude Desktop] -->|6. SSE接続| Server
    LLM -->|7. ツールリクエスト| Server
    Server -->|8. GA4 APIリクエスト| GA4[Google Analytics API]
    GA4 -->|9. データ応答| Server
    Server -->|10. ツール応答| LLM

    subgraph "GA4 MCPサーバー"
    OAuth2Module[OAuth2認証モジュール]
    MCPServer[FastMCP (SSEモード)]
    GA4Tools[GA4ツール]
    end

    OAuth2Module -->|認証情報提供| GA4Tools
    MCPServer -->|ツール実行| GA4Tools
```

## 主要な変更点

### 1. OAuth2認証の実装

既存のサービスアカウント認証から、OAuth2認証に変更します。以下の機能を実装します：

- OAuth2認証フローの初期化
- 認証URLの生成と表示
- 認証コードの受け取りと処理
- アクセストークンとリフレッシュトークンの管理
- トークンの保存と読み込み
- トークンの自動更新

### 2. SSEモードへの変更

FastMCPライブラリのSSEモードを使用して、HTTP-SSE通信をサポートします。以下の変更を行います：

- `mcp.run("stdio")` から `mcp.run("sse")` への変更
- SSEサーバーの設定（ポート、ホストなど）
- エラーハンドリングの強化

### 3. 設定の変更

以下の設定オプションを追加します：

- **GA4プロパティID**：環境変数または設定ファイルから読み込み
- **認証モード**：OAuth2またはサービスアカウント（後方互換性用）
- **トークンパス**：OAuth2トークンを保存するパス
- **ポート**：SSEサーバーがリッスンするポート（デフォルト: 8000）
- **ホスト**：SSEサーバーがバインドするホスト（デフォルト: localhost）

## データフロー

1. **起動時**：
   - 設定の読み込み
   - 保存されたトークンの確認
   - トークンがない場合、OAuth2認証フローを開始
   - SSEサーバーの起動

2. **認証フロー**：
   - OAuth2認証URLの生成と表示
   - ユーザーがブラウザで認証
   - 認証コードの受け取り
   - アクセストークンとリフレッシュトークンの取得と保存

3. **リクエスト処理**：
   - LLMクライアントからのリクエスト受信
   - トークンの有効性確認（必要に応じて更新）
   - GA4 APIへのリクエスト送信
   - レスポンスの処理と返送

## エラー処理

以下のエラーケースを処理します：

1. **認証エラー**：
   - OAuth2認証の失敗
   - トークンの有効期限切れ
   - 権限不足

2. **接続エラー**：
   - SSE接続の確立失敗
   - 接続の切断
   - タイムアウト

3. **APIエラー**：
   - GA4 APIのエラーレスポンス
   - レート制限
   - 無効なリクエスト

各エラーケースに対して、適切なエラーメッセージとログを提供します。

## 依存関係

以下のPythonパッケージに依存します：

1. **fastmcp>=2.0.0**：MCPプロトコルの実装（既存）
2. **google-analytics-data>=0.16.0**：GA4 APIへのアクセス（既存）
3. **google-auth-oauthlib**：OAuth2認証フローの処理（新規）

## 互換性に関する注意

- 既存のサービスアカウント認証はサポートせず、OAuth2認証のみをサポートします
- MCPサーバーはSSEモードでのみ動作し、従来のstdioモードはサポートしません

## セキュリティ考慮事項

1. **トークンの安全な保存**：
   - トークンはローカルファイルシステムに保存
   - 適切なファイルパーミッションを設定

2. **最小権限の原則**：
   - 必要最小限のスコープのみをリクエスト
   - 不要な権限は要求しない