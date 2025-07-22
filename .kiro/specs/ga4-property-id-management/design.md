# 設計ドキュメント

## 概要

このドキュメントでは、Google Analytics 4 MCPサーバーを拡張して、動的なプロパティID管理をサポートするための設計を詳述します。現在のサーバーは起動時に固定のGA4プロパティIDを指定する必要がありますが、新しい設計ではGoogle Analytics Adminライブラリを使用して利用可能なプロパティの一覧を取得し、LLMがレポート作成時に適切なプロパティIDを指定できるようにします。

既存の `ga4_mcp_server.py` を修正して、以下の主要な変更を行います：

1. Google Analytics Adminライブラリを追加して、プロパティ一覧を取得する機能を実装
2. プロパティ情報を管理するためのクラスを追加
3. GA4データ取得ツールを修正して、プロパティIDを必須パラメータとして要求
4. プロパティ一覧を取得・管理するための新しいツールを追加

## アーキテクチャ

修正後のシステムは以下の主要コンポーネントで構成されます：

1. **GA4プロパティ管理モジュール**：Google Analytics Adminライブラリを使用してプロパティ一覧を取得・管理
2. **既存のMCPサーバー**：FastMCPライブラリを使用して、SSEモードで実行
3. **GA4データアクセス**：プロパティIDを必須パラメータとして受け取るように修正

システムの全体的なアーキテクチャは以下の図のようになります：

```mermaid
graph TD
    User[ユーザー] -->|1. 起動| Server[GA4 MCPサーバー]
    Server -->|2. OAuth認証| Google[Google OAuth]
    LLM[Claude Desktop] -->|3. SSE接続| Server
    LLM -->|4. プロパティ一覧リクエスト| Server
    Server -->|5. Admin APIリクエスト| GA4Admin[Google Analytics Admin API]
    GA4Admin -->|6. プロパティ一覧| Server
    Server -->|7. プロパティ一覧| LLM
    LLM -->|8. プロパティID指定でデータリクエスト| Server
    Server -->|9. GA4 APIリクエスト| GA4[Google Analytics API]
    GA4 -->|10. データ応答| Server
    Server -->|11. データ応答| LLM

    subgraph "GA4 MCPサーバー"
    OAuth2Module[OAuth2認証モジュール]
    PropertyManager[プロパティ管理モジュール]
    MCPServer[FastMCP (SSEモード)]
    GA4Tools[GA4ツール]
    end

    OAuth2Module -->|認証情報提供| PropertyManager
    OAuth2Module -->|認証情報提供| GA4Tools
    PropertyManager -->|プロパティ情報| GA4Tools
    MCPServer -->|ツール実行| GA4Tools
    MCPServer -->|ツール実行| PropertyManager
```

## コンポーネントと機能

### 1. GA4プロパティ管理モジュール

このモジュールは、Google Analytics Adminライブラリを使用してプロパティ一覧を取得・管理します。

```python
class GA4PropertyManager:
    def __init__(self, credentials):
        self.credentials = credentials
        self.properties = {}  # プロパティIDと名前のマッピング
        self.admin_client = None
        self.initialize_client()
    
    def initialize_client(self):
        """Admin APIクライアントを初期化"""
        try:
            from google.analytics.admin import AnalyticsAdminServiceClient
            self.admin_client = AnalyticsAdminServiceClient(credentials=self.credentials)
        except ImportError:
            print("Google Analytics Admin library not installed. Run: pip install google-analytics-admin", file=sys.stderr)
            self.admin_client = None
    
    def list_properties(self):
        """利用可能なGA4プロパティの一覧を取得"""
        if not self.admin_client:
            return {"error": "Google Analytics Admin library not available"}
        
        try:
            properties = []
            for property in self.admin_client.list_properties():
                prop_info = {
                    "id": property.name.split('/')[-1],
                    "display_name": property.display_name,
                    "create_time": property.create_time.isoformat(),
                    "update_time": property.update_time.isoformat(),
                    "parent": property.parent
                }
                properties.append(prop_info)
                # キャッシュに保存
                self.properties[prop_info["id"]] = prop_info
            
            return properties
        except Exception as e:
            return {"error": f"Failed to list properties: {str(e)}"}
    
    def get_property_info(self, property_id):
        """特定のプロパティの詳細情報を取得"""
        # キャッシュにあればそれを返す
        if property_id in self.properties:
            return self.properties[property_id]
        
        # なければAPIから取得
        if not self.admin_client:
            return {"error": "Google Analytics Admin library not available"}
        
        try:
            property = self.admin_client.get_property(name=f"properties/{property_id}")
            prop_info = {
                "id": property.name.split('/')[-1],
                "display_name": property.display_name,
                "create_time": property.create_time.isoformat(),
                "update_time": property.update_time.isoformat(),
                "parent": property.parent
            }
            # キャッシュに保存
            self.properties[prop_info["id"]] = prop_info
            return prop_info
        except Exception as e:
            return {"error": f"Failed to get property info: {str(e)}"}
    
    def validate_property_id(self, property_id):
        """プロパティIDが有効かどうかを確認"""
        if property_id in self.properties:
            return True
        
        # プロパティ一覧を更新して再確認
        self.list_properties()
        return property_id in self.properties
```

### 2. MCPツールの拡張

既存のMCPツールを拡張して、プロパティ管理機能を追加します。

```python
# プロパティ管理モジュールのインスタンスを作成
property_manager = None

# OAuth2認証後にプロパティ管理モジュールを初期化
def initialize_property_manager(credentials):
    global property_manager
    property_manager = GA4PropertyManager(credentials)

# 既存の認証関数を修正
def get_oauth_credentials():
    # 既存のコード...
    
    # 認証情報を取得した後、プロパティ管理モジュールを初期化
    initialize_property_manager(creds)
    
    return creds

# 新しいMCPツール: プロパティ一覧の取得
@mcp.tool()
def list_ga4_properties():
    """
    利用可能なGA4プロパティの一覧を取得します。
    
    Returns:
        List of dictionaries containing property information (id, name, etc.)
    """
    global property_manager
    if not property_manager:
        return {"error": "Property manager not initialized. Authentication may have failed."}
    
    return property_manager.list_properties()

# 新しいMCPツール: プロパティ情報の取得
@mcp.tool()
def get_ga4_property_info(property_id):
    """
    特定のGA4プロパティの詳細情報を取得します。
    
    Args:
        property_id: GA4プロパティID
        
    Returns:
        Dictionary containing property information
    """
    global property_manager
    if not property_manager:
        return {"error": "Property manager not initialized. Authentication may have failed."}
    
    return property_manager.get_property_info(property_id)
```

### 3. 既存のGA4データ取得ツールの修正

既存の `get_ga4_data` ツールを修正して、プロパティIDを必須パラメータとして要求します。

```python
@mcp.tool()
def get_ga4_data(
    property_id,  # 必須パラメータとして追加
    dimensions=["date"],
    metrics=["totalUsers", "newUsers", "bounceRate", "screenPageViewsPerSession", "averageSessionDuration"],
    date_range_start="7daysAgo",
    date_range_end="yesterday",
    dimension_filter=None
):
    """
    Retrieve GA4 metrics data broken down by the specified dimensions.
    
    Args:
        property_id: GA4 property ID (required)
        dimensions: List of GA4 dimensions (e.g., ["date", "city"]) or a string 
                    representation (e.g., "[\"date\", \"city\"]" or "date,city").
        metrics: List of GA4 metrics (e.g., ["totalUsers", "newUsers"]) or a string
                 representation (e.g., "[\"totalUsers\"]" or "totalUsers,newUsers").
        date_range_start: Start date in YYYY-MM-DD format or relative date like '7daysAgo'.
        date_range_end: End date in YYYY-MM-DD format or relative date like 'yesterday'.
        dimension_filter: (Optional) JSON string or dict representing a GA4 FilterExpression. See GA4 API docs for structure.
        
    Returns:
        List of dictionaries containing the requested data, or an error dictionary.
    """
    # プロパティIDの検証
    global property_manager
    if property_manager and not property_manager.validate_property_id(property_id):
        available_properties = property_manager.list_properties()
        if isinstance(available_properties, dict) and "error" in available_properties:
            return {"error": f"Invalid property ID: {property_id}. Failed to get available properties: {available_properties['error']}"}
        else:
            property_ids = [p["id"] for p in available_properties]
            return {"error": f"Invalid property ID: {property_id}. Available property IDs: {property_ids}"}
    
    # 以下は既存のコード（プロパティIDを使用するように修正）
    try:
        # Handle cases where dimensions might be passed as a string from the MCP client
        parsed_dimensions = dimensions
        if isinstance(dimensions, str):
            try:
                parsed_dimensions = json.loads(dimensions)
                if not isinstance(parsed_dimensions, list):
                    parsed_dimensions = [str(parsed_dimensions)]
            except json.JSONDecodeError:
                parsed_dimensions = [d.strip() for d in dimensions.split(',')]
        parsed_dimensions = [str(d).strip() for d in parsed_dimensions if str(d).strip()]

        # Handle cases where metrics might be passed as a string
        parsed_metrics = metrics
        if isinstance(metrics, str):
            try:
                parsed_metrics = json.loads(metrics)
                if not isinstance(parsed_metrics, list):
                    parsed_metrics = [str(parsed_metrics)]
            except json.JSONDecodeError:
                parsed_metrics = [m.strip() for m in metrics.split(',')]
        parsed_metrics = [str(m).strip() for m in parsed_metrics if str(m).strip()]

        # Proceed if we have valid dimensions and metrics after parsing
        if not parsed_dimensions:
            return {"error": "Dimensions list cannot be empty after parsing."}
        if not parsed_metrics:
            return {"error": "Metrics list cannot be empty after parsing."}

        # 以下は既存のコード（プロパティIDを使用するように修正）
        
        # GA4クライアントの取得
        client = get_authenticated_client()
        
        # レポートリクエストの作成（プロパティIDを使用）
        request = RunReportRequest(
            property=f"properties/{property_id}",  # プロパティIDを使用
            dimensions=[Dimension(name=d) for d in parsed_dimensions],
            metrics=[Metric(name=m) for m in parsed_metrics],
            date_ranges=[DateRange(start_date=date_range_start, end_date=date_range_end)]
        )
        
        # フィルタの追加（既存のコード）
        
        # レポートの実行と結果の処理（既存のコード）
        
        # 結果の返却（既存のコード）
        
    except Exception as e:
        return {"error": f"Failed to get GA4 data: {str(e)}"}
```

## データフロー

1. **起動時**：
   - OAuth2認証を実行
   - 認証成功後、プロパティ管理モジュールを初期化
   - Google Analytics Adminライブラリを使用してプロパティ一覧を取得

2. **プロパティ一覧の取得**：
   - LLMが `list_ga4_properties` ツールを呼び出す
   - プロパティ管理モジュールがGoogle Analytics Admin APIを使用してプロパティ一覧を取得
   - 取得したプロパティ情報をキャッシュに保存
   - プロパティ一覧をLLMに返す

3. **GA4データの取得**：
   - LLMが `get_ga4_data` ツールを呼び出し、プロパティIDを指定
   - プロパティ管理モジュールがプロパティIDを検証
   - 有効なプロパティIDであれば、GA4 APIを使用してデータを取得
   - 無効なプロパティIDであれば、エラーメッセージと有効なプロパティIDの一覧を返す

## エラー処理

以下のエラーケースを処理します：

1. **Google Analytics Adminライブラリが利用できない**：
   - エラーメッセージを表示
   - インストール方法を案内

2. **プロパティ一覧の取得に失敗**：
   - エラーメッセージを表示
   - エラーの詳細をログに記録

3. **無効なプロパティID**：
   - エラーメッセージを表示
   - 有効なプロパティIDの一覧を提供

4. **GA4 APIエラー**：
   - エラーメッセージを表示
   - エラーの詳細をログに記録

## 依存関係

以下のPythonパッケージに依存します：

1. **fastmcp>=2.0.0**：MCPプロトコルの実装（既存）
2. **google-analytics-data>=0.16.0**：GA4 APIへのアクセス（既存）
3. **google-auth-oauthlib**：OAuth2認証フローの処理（既存）
4. **google-analytics-admin>=0.4.0**：GA4プロパティ管理（新規）

## セキュリティ考慮事項

1. **OAuth2スコープの拡張**：
   - Google Analytics Adminライブラリにアクセスするために、OAuth2スコープを拡張
   - 必要最小限のスコープのみをリクエスト

2. **プロパティ情報の保護**：
   - プロパティ情報はメモリ内のみに保存
   - 外部に保存しない

## 互換性に関する注意

- 既存の `get_ga4_data` ツールの呼び出し方法が変更されるため、既存のクライアントは修正が必要
- プロパティIDが必須パラメータとなるため、既存のクライアントは適切なプロパティIDを指定する必要がある