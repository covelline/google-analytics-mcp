from fastmcp import FastMCP
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange, Dimension, Metric, RunReportRequest, Filter, FilterExpression, FilterExpressionList
)
from google.analytics.admin_v1beta import AnalyticsAdminServiceClient
from google.analytics.admin_v1beta.types import (
    ListAccountSummariesRequest
)
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
import os
import sys
import json
import argparse
import webbrowser
import pickle
from pathlib import Path
import logging
import threading
import socket
from http.server import HTTPServer, BaseHTTPRequestHandler

# OAuth2 configuration
SCOPES = [
    'https://www.googleapis.com/auth/analytics.readonly',  # For Analytics Data API
]

# Global variables for configuration
GA4_PROPERTY_ID = None
CREDENTIALS = None
TOKEN_PATH = 'token.pickle'
CLIENT_SECRETS = {
    "installed": {
        "client_id": "your-client-id.apps.googleusercontent.com",
        "client_secret": "your-client-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost:8080"]
    }
}

# Property management module
property_manager = None

# GA4 Property Manager class
class GA4PropertyManager:
    """Manages GA4 properties using the Google Analytics Admin API."""
    
    def __init__(self, credentials):
        """Initialize the property manager with OAuth2 credentials.
        
        Args:
            credentials: OAuth2 credentials for Google Analytics APIs.
        """
        self.credentials = credentials
        self.properties = {}  # Property ID to property info mapping (cache)
        self.admin_client = None
        self.initialize_client()
    
    def initialize_client(self):
        """Initialize the Analytics Admin API client."""
        
        try:
            self.admin_client = AnalyticsAdminServiceClient(credentials=self.credentials)
            print("Analytics Admin API client initialized successfully", file=sys.stderr)
        except Exception as e:
            print(f"Failed to initialize Analytics Admin API client: {e}", file=sys.stderr)
            self.admin_client = None
    
    def list_properties(self):
        """List all available GA4 properties the user has access to.
        
        Returns:
            List of dictionaries containing property information, or an error dictionary.
        """
        if not self.admin_client:
            return {"error": "Google Analytics Admin API client not available."
                    + " Install google-analytics-admin package and ensure proper credentials."}
        
        try:
            properties = []
            next_page_token = ""
            while True:
                # accountSummariesリクエストを送信
                request = ListAccountSummariesRequest(page_size=200, page_token=next_page_token)
                response = self.admin_client.list_account_summaries(request=request)
                
                # 各アカウントサマリーを処理
                for account_summary in response.account_summaries:
                    # 各アカウントに含まれるプロパティサマリーを処理
                    for property_summary in account_summary.property_summaries:
                        # プロパティIDを抽出（形式: properties/{property_id}）
                        prop_id = property_summary.property.split('/')[-1]
                        
                        # プロパティ情報辞書を作成
                        prop_info = {
                            "id": prop_id,
                            "display_name": property_summary.display_name,
                            # PropertySummaryにはcreate_timeとupdate_timeがないため、Noneを設定
                            "create_time": None,
                            "update_time": None,
                            "parent": account_summary.account,  # 親アカウント情報を設定
                            "account_name": account_summary.display_name  # アカウント名も追加
                        }
                        
                        # 結果に追加しキャッシュを更新
                        properties.append(prop_info)
                        self.properties[prop_id] = prop_info
                
                # 次のページがあるかチェック
                next_page_token = response.next_page_token
                if not next_page_token:
                    break
            
            return properties
        except Exception as e:
            error_message = f"Failed to list GA4 properties: {str(e)}"
            print(error_message, file=sys.stderr)
            return {"error": error_message}
    
    def get_property_info(self, property_id):
        """Get detailed information about a specific GA4 property.
        
        Args:
            property_id: The ID of the GA4 property.
            
        Returns:
            Dictionary containing property information, or an error dictionary.
        """
        # Check cache first
        if property_id in self.properties:
            return self.properties[property_id]
        
        # Not in cache, fetch from API
        if not self.admin_client:
            return {"error": "Google Analytics Admin API client not available."}
        
        try:
            property = self.admin_client.get_property(name=f"properties/{property_id}")
            
            # Extract property ID from the full resource name
            prop_id = property.name.split('/')[-1]
            
            # Create property info dictionary
            prop_info = {
                "id": prop_id,
                "display_name": property.display_name,
                "create_time": property.create_time.isoformat() if property.create_time else None,
                "update_time": property.update_time.isoformat() if property.update_time else None,
                "parent": property.parent
            }
            
            # Update cache and return
            self.properties[prop_id] = prop_info
            return prop_info
        except Exception as e:
            error_message = f"Failed to get GA4 property info: {str(e)}"
            print(error_message, file=sys.stderr)
            return {"error": error_message}
    
    def validate_property_id(self, property_id):
        """Validate that a property ID exists and is accessible.
        
        Args:
            property_id: The ID of the GA4 property to validate.
            
        Returns:
            Boolean indicating whether the property ID is valid.
        """
        # Check cache first
        if property_id in self.properties:
            return True
        
        # Not in cache, try to fetch property info
        try:
            property_info = self.get_property_info(property_id)
            return "error" not in property_info
        except Exception:
            # If an exception occurs, property ID is invalid
            return False

# Configuration class to hold runtime settings
class Config:
    def __init__(self):
        # property_id is now optional - if not provided, must specify in API calls
        self.property_id = None  
        self.token_path = 'token.pickle'
        self.port = 8000
        self.host = 'localhost'
        self.client_id = None
        self.client_secret = None
        self.setup_mode = False
        
config = Config()

# Initialize FastMCP
mcp = FastMCP("Google Analytics 4")

# OAuth2 authentication functions
def get_oauth_credentials():
    """Get OAuth2 credentials, handling the full flow if needed."""
    creds = None
    
    # Check if token.pickle file exists
    if os.path.exists(config.token_path):
        with open(config.token_path, 'rb') as token:
            creds = pickle.load(token)
    
    # If there are no (valid) credentials available, let the user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                print("Refreshing expired OAuth token...", file=sys.stderr)
                creds.refresh(Request())
            except Exception as e:
                print(f"Token refresh failed: {e}", file=sys.stderr)
                creds = None
        
        if not creds:
            if not config.client_id or not config.client_secret:
                print("\n" + "="*60, file=sys.stderr)
                print("OAUTH2 SETUP REQUIRED", file=sys.stderr)
                print("="*60, file=sys.stderr)
                print("\nTo use this GA4 MCP server, you need to set up OAuth2 credentials.", file=sys.stderr)
                print("\n1. Go to Google Cloud Console:", file=sys.stderr)
                print("   https://console.cloud.google.com/apis/credentials", file=sys.stderr)
                print("\n2. Create OAuth2 credentials for a 'Desktop Application'", file=sys.stderr)
                print("\n3. Download the JSON file and extract:", file=sys.stderr)
                print("   - client_id", file=sys.stderr)
                print("   - client_secret", file=sys.stderr)
                print("\n4. Set these as environment variables:", file=sys.stderr)
                print("   export GOOGLE_OAUTH_CLIENT_ID='your-client-id'", file=sys.stderr)
                print("   export GOOGLE_OAUTH_CLIENT_SECRET='your-client-secret'", file=sys.stderr)
                print("\n5. Restart the server", file=sys.stderr)
                print("="*60, file=sys.stderr)
                sys.exit(1)
            
            # Update CLIENT_SECRETS with provided credentials
            CLIENT_SECRETS['installed']['client_id'] = config.client_id
            CLIENT_SECRETS['installed']['client_secret'] = config.client_secret
            
            print("\nStarting OAuth2 authentication flow...", file=sys.stderr)
            print("A browser window will open for you to authorize this application.", file=sys.stderr)
            print("Note: We're requesting additional permissions to access GA4 property information.", file=sys.stderr)
            
            flow = InstalledAppFlow.from_client_config(CLIENT_SECRETS, SCOPES)
            creds = flow.run_local_server(port=8080)
            
        # Save the credentials for the next run
        with open(config.token_path, 'wb') as token:
            pickle.dump(creds, token)
            print(f"OAuth2 credentials saved to {config.token_path}", file=sys.stderr)
    
    # Initialize the property manager with the credentials
    initialize_property_manager(creds)
    
    return creds

def initialize_property_manager(credentials):
    """Initialize the GA4 Property Manager with OAuth2 credentials.
    
    Args:
        credentials: OAuth2 credentials for Google Analytics APIs.
    """
    global property_manager
    
    try:
        property_manager = GA4PropertyManager(credentials)
        print("GA4 Property Manager initialized", file=sys.stderr)
        
        # If a default property ID is provided, verify it exists
        if config.property_id and property_manager:
            valid = property_manager.validate_property_id(config.property_id)
            if valid:
                print(f"Verified default property ID: {config.property_id}", file=sys.stderr)
            else:
                print(f"Warning: Default property ID {config.property_id} may not be valid or accessible", file=sys.stderr)
                print("Use list_ga4_properties tool to see available properties", file=sys.stderr)
    except Exception as e:
        print(f"Failed to initialize Property Manager: {e}", file=sys.stderr)
        property_manager = None

def get_authenticated_client():
    """Get an authenticated GA4 client using OAuth2."""
    credentials = get_oauth_credentials()
    return BetaAnalyticsDataClient(credentials=credentials)

# Embedded GA4 Dimensions Data
GA4_DIMENSIONS = {
    "time": {
        "date": "The date of the event in YYYYMMDD format.",
        "dateHour": "The date and hour of the event in YYYYMMDDHH format.",
        "dateHourMinute": "The date, hour, and minute of the event in YYYYMMDDHHMM format.",
        "day": "The day of the month (01-31).",
        "dayOfWeek": "The day of the week (0-6, where Sunday is 0).",
        "hour": "The hour of the day (00-23).",
        "minute": "The minute of the hour (00-59).",
        "month": "The month of the year (01-12).",
        "week": "The week of the year (00-53).",
        "year": "The year (e.g., 2024).",
        "nthDay": "The number of days since the first visit.",
        "nthHour": "The number of hours since the first visit.",
        "nthMinute": "The number of minutes since the first visit.",
        "nthMonth": "The number of months since the first visit.",
        "nthWeek": "The number of weeks since the first visit.",
        "nthYear": "The number of years since the first visit."
    },
    "geography": {
        "city": "The city of the user.",
        "cityId": "The ID of the city.",
        "country": "The country of the user.",
        "countryId": "The ID of the country.",
        "region": "The region of the user."
    },
    "technology": {
        "browser": "The browser used by the user.",
        "deviceCategory": "The category of the device (e.g., 'desktop', 'mobile', 'tablet').",
        "deviceModel": "The model of the device.",
        "operatingSystem": "The operating system of the user's device.",
        "operatingSystemVersion": "The version of the operating system.",
        "platform": "The platform of the user's device (e.g., 'web', 'android', 'ios').",
        "platformDeviceCategory": "The platform and device category.",
        "screenResolution": "The resolution of the user's screen."
    },
    "traffic_source": {
        "campaignId": "The ID of the campaign.",
        "campaignName": "The name of the campaign.",
        "defaultChannelGroup": "The default channel grouping for the traffic source.",
        "medium": "The medium of the traffic source.",
        "source": "The source of the traffic.",
        "sourceMedium": "The source and medium of the traffic.",
        "sourcePlatform": "The source platform of the traffic.",
        "sessionCampaignId": "The campaign ID of the session.",
        "sessionCampaignName": "The campaign name of the session.",
        "sessionDefaultChannelGroup": "The default channel group of the session.",
        "sessionMedium": "The medium of the session.",
        "sessionSource": "The source of the session.",
        "sessionSourceMedium": "The source and medium of the session.",
        "sessionSourcePlatform": "The source platform of the session."
    },
    "first_user_attribution": {
        "firstUserCampaignId": "The campaign ID that first acquired the user.",
        "firstUserCampaignName": "The campaign name that first acquired the user.",
        "firstUserDefaultChannelGroup": "The default channel group that first acquired the user.",
        "firstUserMedium": "The medium that first acquired the user.",
        "firstUserSource": "The source that first acquired the user.",
        "firstUserSourceMedium": "The source and medium that first acquired the user.",
        "firstUserSourcePlatform": "The source platform that first acquired the user."
    },
    "content": {
        "contentGroup": "The content group on your site/app. Populated by the event parameter 'content_group'.",
        "contentId": "The ID of the content. Populated by the event parameter 'content_id'.",
        "contentType": "The type of content. Populated by the event parameter 'content_type'.",
        "fullPageUrl": "The full URL of the page.",
        "landingPage": "The page path of the landing page.",
        "pageLocation": "The full URL of the page.",
        "pagePath": "The path of the page (e.g., '/home').",
        "pagePathPlusQueryString": "The page path and query string.",
        "pageReferrer": "The referring URL.",
        "pageTitle": "The title of the page.",
        "unifiedScreenClass": "The class of the screen.",
        "unifiedScreenName": "The name of the screen."
    },
    "events": {
        "eventName": "The name of the event.",
        "isConversionEvent": "Whether the event is a conversion event ('true' or 'false').",
        "method": "The method of the event. Populated by the event parameter 'method'."
    },
    "ecommerce": {
        "itemBrand": "The brand of the item.",
        "itemCategory": "The category of the item.",
        "itemCategory2": "A secondary category for the item.",
        "itemCategory3": "A third category for the item.",
        "itemCategory4": "A fourth category for the item.",
        "itemCategory5": "A fifth category for the item.",
        "itemId": "The ID of the item.",
        "itemListId": "The ID of the item list.",
        "itemListName": "The name of the item list.",
        "itemName": "The name of the item.",
        "itemPromotionCreativeName": "The creative name of the item promotion.",
        "itemPromotionId": "The ID of the item promotion.",
        "itemPromotionName": "The name of the item promotion.",
        "orderCoupon": "The coupon code for the order.",
        "shippingTier": "The shipping tier for the order.",
        "transactionId": "The ID of the transaction."
    },
    "user_demographics": {
        "newVsReturning": "Whether the user is new or returning.",
        "signedInWithUserId": "Whether the user was signed in with a User-ID ('true' or 'false').",
        "userAgeBracket": "The age bracket of the user.",
        "userGender": "The gender of the user.",
        "language": "The language of the user's browser or device.",
        "languageCode": "The language code."
    },
    "google_ads": {
        "googleAdsAdGroupId": "The ID of the Google Ads ad group.",
        "googleAdsAdGroupName": "The name of the Google Ads ad group.",
        "googleAdsAdNetworkType": "The ad network type in Google Ads.",
        "googleAdsCampaignId": "The ID of the Google Ads campaign.",
        "googleAdsCampaignName": "The name of the Google Ads campaign.",
        "googleAdsCampaignType": "The type of the Google Ads campaign.",
        "googleAdsCreativeId": "The ID of the Google Ads creative.",
        "googleAdsKeyword": "The keyword from Google Ads.",
        "googleAdsQuery": "The search query from Google Ads.",
        "firstUserGoogleAdsAdGroupId": "The Google Ads ad group ID that first acquired the user.",
        "firstUserGoogleAdsAdGroupName": "The Google Ads ad group name that first acquired the user.",
        "firstUserGoogleAdsCampaignId": "The Google Ads campaign ID that first acquired the user.",
        "firstUserGoogleAdsCampaignName": "The Google Ads campaign name that first acquired the user.",
        "firstUserGoogleAdsCampaignType": "The Google Ads campaign type that first acquired the user.",
        "firstUserGoogleAdsCreativeId": "The Google Ads creative ID that first acquired the user.",
        "firstUserGoogleAdsKeyword": "The Google Ads keyword that first acquired the user.",
        "firstUserGoogleAdsNetworkType": "The Google Ads network type that first acquired the user.",
        "firstUserGoogleAdsQuery": "The Google Ads query that first acquired the user.",
        "sessionGoogleAdsAdGroupId": "The Google Ads ad group ID of the session.",
        "sessionGoogleAdsAdGroupName": "The Google Ads ad group name of the session.",
        "sessionGoogleAdsCampaignId": "The Google Ads campaign ID of the session.",
        "sessionGoogleAdsCampaignName": "The Google Ads campaign name of the session.",
        "sessionGoogleAdsCampaignType": "The Google Ads campaign type of the session.",
        "sessionGoogleAdsCreativeId": "The Google Ads creative ID of the session.",
        "sessionGoogleAdsKeyword": "The Google Ads keyword of the session.",
        "sessionGoogleAdsNetworkType": "The Google Ads network type of the session.",
        "sessionGoogleAdsQuery": "The Google Ads query of the session."
    },
    "manual_campaigns": {
        "manualAdContent": "The ad content from a manual campaign.",
        "manualTerm": "The term from a manual campaign.",
        "firstUserManualAdContent": "The manual ad content that first acquired the user.",
        "firstUserManualTerm": "The manual term that first acquired the user.",
        "sessionManualAdContent": "The manual ad content of the session.",
        "sessionManualTerm": "The manual term of the session."
    },
    "app_specific": {
        "appVersion": "The version of the app.",
        "streamId": "The ID of the data stream.",
        "streamName": "The name of the data stream."
    },
    "cohort_analysis": {
        "cohort": "The cohort the user belongs to.",
        "cohortNthDay": "The day number within the cohort.",
        "cohortNthMonth": "The month number within the cohort.",
        "cohortNthWeek": "The week number within the cohort."
    },
    "audiences": {
        "audienceId": "The ID of the audience.",
        "audienceName": "The name of the audience.",
        "brandingInterest": "The interest category associated with the user."
    },
    "enhanced_measurement": {
        "fileExtension": "The extension of the downloaded file.",
        "fileName": "The name of the downloaded file.",
        "linkClasses": "The classes of the clicked link.",
        "linkDomain": "The domain of the clicked link.",
        "linkId": "The ID of the clicked link.",
        "linkText": "The text of the clicked link.",
        "linkUrl": "The URL of the clicked link.",
        "outbound": "Whether the clicked link was outbound ('true' or 'false').",
        "percentScrolled": "The percentage of the page scrolled.",
        "searchTerm": "The term used for an internal site search.",
        "videoProvider": "The provider of the video.",
        "videoTitle": "The title of the video.",
        "videoUrl": "The URL of the video.",
        "visible": "Whether the video was visible on the screen."
    },
    "gaming": {
        "achievementId": "The achievement ID in a game for an event.",
        "character": "The character in a game.",
        "groupId": "The group ID in a game.",
        "virtualCurrencyName": "The name of the virtual currency."
    },
    "advertising": {
        "adFormat": "The format of the ad that was shown (e.g., 'Interstitial', 'Banner', 'Rewarded').",
        "adSourceName": "The name of the ad network or source that served the ad.",
        "adUnitName": "The name of the ad unit that displayed the ad."
    },
    "testing": {
        "testDataFilterName": "The name of the test data filter."
    }
}

# Embedded GA4 Metrics Data
GA4_METRICS = {
    "user_metrics": {
        "totalUsers": "The total number of unique users.",
        "newUsers": "The number of users who interacted with your site or app for the first time.",
        "activeUsers": "The number of distinct users who have logged an engaged session on your site or app.",
        "active1DayUsers": "The number of distinct users who have been active on your site or app in the last 1 day.",
        "active7DayUsers": "The number of distinct users who have been active on your site or app in the last 7 days.",
        "active28DayUsers": "The number of distinct users who have been active on your site or app in the last 28 days.",
        "userStickiness": "A measure of how frequently users return to your site or app.",
        "dauPerMau": "The ratio of daily active users to monthly active users.",
        "dauPerWau": "The ratio of daily active users to weekly active users.",
        "wauPerMau": "The ratio of weekly active users to monthly active users."
    },
    "session_metrics": {
        "sessions": "The total number of sessions.",
        "sessionsPerUser": "The average number of sessions per user.",
        "engagedSessions": "The number of sessions that lasted longer than 10 seconds, or had a conversion event, or had at least 2 pageviews or screenviews.",
        "bounceRate": "The percentage of sessions that were not engaged.",
        "engagementRate": "The percentage of sessions that were engaged.",
        "averageSessionDuration": "The average duration of a session in seconds.",
        "sessionConversionRate": "The percentage of sessions in which a conversion event occurred."
    },
    "pageview_metrics": {
        "screenPageViews": "The total number of app screens or web pages your users saw.",
        "screenPageViewsPerSession": "The average number of screens or pages viewed per session.",
        "screenPageViewsPerUser": "The average number of screens or pages viewed per user."
    },
    "event_metrics": {
        "eventCount": "The total number of events.",
        "eventCountPerUser": "The average number of events per user.",
        "eventsPerSession": "The average number of events per session.",
        "eventValue": "The total value of all 'value' event parameters.",
        "conversions": "The total number of conversion events.",
        "userConversionRate": "The percentage of active users who triggered a conversion event."
    },
    "engagement_metrics": {
        "userEngagementDuration": "The average time your app was in the foreground or your website was in focus in the browser.",
        "scrolledUsers": "The number of users who scrolled at least 90% of the page."
    },
    "ecommerce_metrics": {
        "totalRevenue": "The total revenue from all sources.",
        "purchaseRevenue": "The total revenue from purchases.",
        "grossPurchaseRevenue": "The total purchase revenue, before refunds.",
        "itemRevenue": "The total revenue from items.",
        "grossItemRevenue": "The total revenue from items, before refunds.",
        "averageRevenue": "The average revenue per user.",
        "averagePurchaseRevenue": "The average purchase revenue per user.",
        "averagePurchaseRevenuePerPayingUser": "The average purchase revenue per paying user.",
        "transactions": "The total number of transactions.",
        "ecommercePurchases": "The total number of ecommerce purchases.",
        "purchasers": "The number of users who made a purchase.",
        "totalPurchasers": "The total number of unique purchasers.",
        "purchaserConversionRate": "The percentage of active users who made a purchase.",
        "firstTimePurchasers": "The number of users who made their first purchase.",
        "firstTimePurchaserConversionRate": "The percentage of active users who made their first purchase.",
        "firstTimePurchasersPerNewUser": "The number of first-time purchasers per new user.",
        "transactionsPerPurchaser": "The average number of transactions per purchaser.",
        "checkouts": "The number of times users started the checkout process.",
        "refunds": "The total number of refunds.",
        "refundAmount": "The total amount of refunds.",
        "shippingAmount": "The total shipping cost.",
        "taxAmount": "The total tax amount."
    },
    "item_metrics": {
        "itemViews": "The number of times users viewed items.",
        "itemsAddedToCart": "The number of units of items added to the cart.",
        "itemsCheckedOut": "The number of units of items in the checkout process.",
        "itemPurchaseQuantity": "The total number of units of items purchased.",
        "itemViewToPurchaseRate": "The rate at which users who viewed items also purchased them.",
        "purchaseToViewRate": "The rate at which users who viewed items also purchased them.",
        "itemListViews": "The number of times users viewed item lists.",
        "itemListClicks": "The number of times users clicked on items in a list.",
        "itemListClickThroughRate": "The rate at which users clicked on items in a list.",
        "itemsClickedInList": "The number of units of items clicked in a list.",
        "itemsViewedInList": "The number of units of items viewed in a list.",
        "itemPromotionViews": "The number of times users viewed item promotions.",
        "itemPromotionClicks": "The number of times users clicked on item promotions.",
        "itemPromotionClickThroughRate": "The rate at which users clicked on item promotions.",
        "itemsClickedInPromotion": "The number of units of items clicked in a promotion.",
        "itemsViewedInPromotion": "The number of units of items viewed in a promotion."
    },
    "advertising_metrics": {
        "totalAdRevenue": "The total revenue from all ad sources.",
        "adRevenue": "The total revenue from ads.",
        "adImpressions": "The total number of ad impressions.",
        "publisherAdRevenue": "The total revenue from publisher ads.",
        "publisherAdImpressions": "The total number of publisher ad impressions.",
        "publisherAdClicks": "The total number of clicks on publisher ads.",
        "returnOnAdSpend": "The return on investment from your advertising."
    },
    "search_console_metrics": {
        "organicGoogleSearchClicks": "The number of clicks your website received from organic Google Search.",
        "organicGoogleSearchImpressions": "The number of times your website appeared in organic Google Search results.",
        "organicGoogleSearchClickThroughRate": "The click-through rate for your website in organic Google Search results.",
        "organicGoogleSearchAveragePosition": "The average ranking of your website URLs for the queries reported in Search Console."
    },
    "cohort_metrics": {
        "cohortActiveUsers": "The number of active users in a cohort.",
        "cohortTotalUsers": "The total number of users in a cohort."
    },
    "app_crash_metrics": {
        "crashAffectedUsers": "The number of users who experienced a crash.",
        "crashFreeUsersRate": "The percentage of users who did not experience a crash."
    }
}

# Load functions now use embedded data
def load_dimensions():
    """Load available dimensions from embedded data"""
    return GA4_DIMENSIONS

def load_metrics():
    """Load available metrics from embedded data"""
    return GA4_METRICS

@mcp.tool()
def list_ga4_properties():
    """
    List all available GA4 properties the user has access to.
    
    Use this tool to discover all GA4 properties available to you.
    The returned list includes property IDs which are required for data access.
    
    Returns:
        List of dictionaries containing property information:
        - id: The GA4 property ID (required for data access)
        - display_name: The display name of the property
        - create_time: When the property was created
        - update_time: When the property was last updated
        - parent: The parent account information
    """
    global property_manager
    
    if not property_manager:
        return {"error": "Property manager not initialized. Authentication may have failed."}
    
    return property_manager.list_properties()

@mcp.tool()
def list_dimension_categories():
    """
    List all available GA4 dimension categories with descriptions.
    
    Returns:
        Dictionary of dimension categories and their available dimensions.
    """
    dimensions = load_dimensions()
    result = {}
    for category, dims in dimensions.items():
        result[category] = {
            "count": len(dims),
            "dimensions": list(dims.keys())
        }
    return result

@mcp.tool()
def list_metric_categories():
    """
    List all available GA4 metric categories with descriptions.
    
    Returns:
        Dictionary of metric categories and their available metrics.
    """
    metrics = load_metrics()
    result = {}
    for category, mets in metrics.items():
        result[category] = {
            "count": len(mets),
            "metrics": list(mets.keys())
        }
    return result

@mcp.tool()
def get_dimensions_by_category(category):
    """
    Get all dimensions in a specific category with their descriptions.
    
    Args:
        category: Category name (e.g., 'time', 'geography', 'ecommerce')
        
    Returns:
        Dictionary of dimensions and their descriptions for the category.
    """
    dimensions = load_dimensions()
    if category in dimensions:
        return dimensions[category]
    else:
        available_categories = list(dimensions.keys())
        return {"error": f"Category '{category}' not found. Available categories: {available_categories}"}

@mcp.tool()
def get_ga4_property_info(property_id):
    """
    Get detailed information about a specific GA4 property.
    
    Use this tool to get information about a specific property when you know its ID.
    
    Args:
        property_id: The ID of the GA4 property to retrieve information for
        
    Returns:
        Dictionary containing property information:
        - id: The GA4 property ID
        - display_name: The display name of the property
        - create_time: When the property was created
        - update_time: When the property was last updated
        - parent: The parent account information
    """
    global property_manager
    
    if not property_manager:
        return {"error": "Property manager not initialized. Authentication may have failed."}
    
    return property_manager.get_property_info(property_id)

@mcp.tool()
def get_metrics_by_category(category):
    """
    Get all metrics in a specific category with their descriptions.
    
    Args:
        category: Category name (e.g., 'user_metrics', 'ecommerce_metrics', 'session_metrics')
        
    Returns:
        Dictionary of metrics and their descriptions for the category.
    """
    metrics = load_metrics()
    if category in metrics:
        return metrics[category]
    else:
        available_categories = list(metrics.keys())
        return {"error": f"Category '{category}' not found. Available categories: {available_categories}"}

@mcp.tool()
def get_ga4_data(
    property_id,
    dimensions=["date"],
    metrics=["totalUsers", "newUsers", "bounceRate", "screenPageViewsPerSession", "averageSessionDuration"],
    date_range_start="7daysAgo",
    date_range_end="yesterday",
    dimension_filter=None
):
    """
    Retrieve GA4 metrics data broken down by the specified dimensions.
    
    Args:
        property_id: The GA4 property ID to query data from (required).
                     Use list_ga4_properties to get available property IDs.
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
    try:
        # Validate that property_id is provided and valid
        if not property_id:
            return {"error": "property_id is required. Use list_ga4_properties tool to get available property IDs."}
        
        # Validate the property ID if property manager is available
        global property_manager
        if property_manager:
            valid_property = property_manager.validate_property_id(property_id)
            if not valid_property:
                # Try to get a list of available properties to help the user
                properties = property_manager.list_properties()
                if isinstance(properties, list) and properties:
                    available_ids = [p["id"] for p in properties]
                    return {"error": f"Invalid property ID: {property_id}. Available property IDs: {available_ids}"}
                else:
                    return {"error": f"Invalid property ID: {property_id}. Use list_ga4_properties tool to get available property IDs."}
        
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

        # Validate dimension_filter and build FilterExpression if provided
        filter_expression = None
        if dimension_filter:
            print(f"DEBUG: Processing dimension_filter: {dimension_filter}", file=sys.stderr)
            
            # Load valid dimensions from embedded data
            valid_dimensions = set()
            dims_json = load_dimensions()
            for cat in dims_json.values():
                valid_dimensions.update(cat.keys())
            
            # Parse filter input
            if isinstance(dimension_filter, str):
                try:
                    filter_dict = json.loads(dimension_filter)
                except Exception as e:
                    return {"error": f"Failed to parse dimension_filter JSON: {e}"}
            elif isinstance(dimension_filter, dict):
                filter_dict = dimension_filter
            else:
                return {"error": "dimension_filter must be a JSON string or dict."}

            # Recursive helper to build FilterExpression from dict
            def build_filter_expr(expr):
                try:
                    if 'andGroup' in expr:
                        expressions = []
                        for e in expr['andGroup']['expressions']:
                            built_expr = build_filter_expr(e)
                            if built_expr is None:
                                return None
                            expressions.append(built_expr)
                        return FilterExpression(and_group=FilterExpressionList(expressions=expressions))
                    
                    if 'orGroup' in expr:
                        expressions = []
                        for e in expr['orGroup']['expressions']:
                            built_expr = build_filter_expr(e)
                            if built_expr is None:
                                return None
                            expressions.append(built_expr)
                        return FilterExpression(or_group=FilterExpressionList(expressions=expressions))
                    
                    if 'notExpression' in expr:
                        built_expr = build_filter_expr(expr['notExpression'])
                        if built_expr is None:
                            return None
                        return FilterExpression(not_expression=built_expr)
                    
                    if 'filter' in expr:
                        f = expr['filter']
                        field = f.get('fieldName')
                        if not field:
                            print(f"DEBUG: Missing fieldName in filter: {f}", file=sys.stderr)
                            return None
                        if field not in valid_dimensions:
                            print(f"DEBUG: Invalid dimension '{field}'. Valid: {sorted(list(valid_dimensions))[:10]}...", file=sys.stderr)
                            return None
                        
                        if 'stringFilter' in f:
                            sf = f['stringFilter']
                            # Map string match types to API enum values
                            match_type_map = {
                                'EXACT': Filter.StringFilter.MatchType.EXACT,
                                'BEGINS_WITH': Filter.StringFilter.MatchType.BEGINS_WITH,
                                'ENDS_WITH': Filter.StringFilter.MatchType.ENDS_WITH,
                                'CONTAINS': Filter.StringFilter.MatchType.CONTAINS,
                                'FULL_REGEXP': Filter.StringFilter.MatchType.FULL_REGEXP,
                                'PARTIAL_REGEXP': Filter.StringFilter.MatchType.PARTIAL_REGEXP
                            }
                            match_type = match_type_map.get(sf.get('matchType', 'EXACT'), Filter.StringFilter.MatchType.EXACT)
                            
                            return FilterExpression(filter=Filter(
                                field_name=field,
                                string_filter=Filter.StringFilter(
                                    value=sf.get('value', ''),
                                    match_type=match_type,
                                    case_sensitive=sf.get('caseSensitive', False)
                                )
                            ))
                        
                        if 'inListFilter' in f:
                            ilf = f['inListFilter']
                            return FilterExpression(filter=Filter(
                                field_name=field,
                                in_list_filter=Filter.InListFilter(
                                    values=ilf.get('values', []),
                                    case_sensitive=ilf.get('caseSensitive', False)
                                )
                            ))
                    
                    print(f"DEBUG: Unrecognized filter structure: {expr}", file=sys.stderr)
                    return None
                    
                except Exception as e:
                    print(f"DEBUG: Exception in build_filter_expr: {e}", file=sys.stderr)
                    return None
            
            filter_expression = build_filter_expr(filter_dict)
            if filter_expression is None:
                return {"error": "Invalid or unsupported dimension_filter structure, or invalid dimension name."}

        # GA4 API Call with OAuth2
        client = get_authenticated_client()
        dimension_objects = [Dimension(name=d) for d in parsed_dimensions]
        metric_objects = [Metric(name=m) for m in parsed_metrics]
        request = RunReportRequest(
            property=f"properties/{property_id}",  # Use the provided property_id parameter
            dimensions=dimension_objects,
            metrics=metric_objects,
            date_ranges=[DateRange(start_date=date_range_start, end_date=date_range_end)],
            dimension_filter=filter_expression if filter_expression else None
        )
        response = client.run_report(request)
        result = []
        for row_idx, row in enumerate(response.rows):
            data_row = {}
            for i, dimension_header in enumerate(response.dimension_headers):
                if i < len(row.dimension_values):
                    data_row[dimension_header.name] = row.dimension_values[i].value
                else:
                    data_row[dimension_header.name] = None
            for i, metric_header in enumerate(response.metric_headers):
                if i < len(row.metric_values):
                    data_row[metric_header.name] = row.metric_values[i].value
                else:
                    data_row[metric_header.name] = None
            result.append(data_row)
        return result
    except Exception as e:
        error_message = f"Error fetching GA4 data: {str(e)}"
        print(error_message, file=sys.stderr)
        
        # Check for specific error patterns to provide more helpful messages
        error_str = str(e).lower()
        if "property not found" in error_str or "invalid resource name" in error_str:
            if property_id is None:
                error_message = "property_id parameter is required. Use list_ga4_properties to get available property IDs."
            else:
                error_message = f"Property ID '{property_id}' not found or not accessible. Use list_ga4_properties to get available property IDs."
        
        if hasattr(e, 'details'):
            error_message += f" Details: {e.details()}"
            
        return {"error": error_message}

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='Google Analytics 4 MCP Server with OAuth2 and SSE support')
    parser.add_argument('--token-path', default='token.pickle',
                       help='Path to store OAuth2 token (default: token.pickle)')
    parser.add_argument('--port', type=int, default=8000,
                       help='Port for SSE server (default: 8000)')
    parser.add_argument('--host', default='localhost',
                       help='Host for SSE server (default: localhost)')
    parser.add_argument('--client-id',
                       help='Google OAuth2 Client ID (can also be set via GOOGLE_OAUTH_CLIENT_ID env var)')
    parser.add_argument('--client-secret',
                       help='Google OAuth2 Client Secret (can also be set via GOOGLE_OAUTH_CLIENT_SECRET env var)')
    parser.add_argument('--setup', action='store_true',
                       help='Run initial OAuth2 setup')
    parser.add_argument('--transport', choices=['stdio', 'sse'], default='sse',
                       help='Transport mode: stdio or sse (default: sse)')
    return parser.parse_args()

def validate_configuration():
    """Validate that all required configuration is present."""

    # Check for OAuth credentials
    if not config.client_id:
        config.client_id = os.getenv('GOOGLE_OAUTH_CLIENT_ID')
    
    if not config.client_secret:
        config.client_secret = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET')
    
    return True

def main():
    """Main entry point for the MCP server"""
    args = parse_args()
    
    # Configure from args and environment
    config.token_path = args.token_path
    config.port = args.port
    config.host = args.host
    config.client_id = args.client_id
    config.client_secret = args.client_secret
    config.setup_mode = args.setup
    
    # Validate configuration
    if not validate_configuration():
        sys.exit(1)
    
    if config.setup_mode:
        print("Running OAuth2 setup...", file=sys.stderr)
        try:
            get_oauth_credentials()
            print("OAuth2 setup completed successfully!", file=sys.stderr)
            print(f"Token saved to: {config.token_path}", file=sys.stderr)
            print("\nYou can now start the server without --setup flag.", file=sys.stderr)
        except Exception as e:
            print(f"OAuth2 setup failed: {e}", file=sys.stderr)
            sys.exit(1)
        return
    
    print(f"Starting GA4 MCP server on {config.host}:{config.port}...", file=sys.stderr)
    print(f"Transport mode: {args.transport}", file=sys.stderr)
    print("Property management enabled: Use list_ga4_properties tool to view available properties", file=sys.stderr)
    
    try:
        # Test OAuth credentials on startup
        get_oauth_credentials()
        print("OAuth2 authentication successful", file=sys.stderr)
        
        # Start the server
        if args.transport == 'sse':
            mcp.run(transport="sse", host=config.host, port=config.port)
        else:
            mcp.run(transport="stdio")
            
    except KeyboardInterrupt:
        print("\nServer stopped by user", file=sys.stderr)
    except Exception as e:
        print(f"Server error: {e}", file=sys.stderr)
        sys.exit(1)

# Start the server when run directly
if __name__ == "__main__":
    main()