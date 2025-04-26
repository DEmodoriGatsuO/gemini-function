import os
import re
import json
from flask import Flask, request, jsonify
import google.cloud.aiplatform as aiplatform
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.auth import default
import vertexai
from vertexai.generative_models import GenerativeModel, Part, GenerationConfig

GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID')
GCP_REGION = os.environ.get('GCP_REGION', 'us-central1')
GEMINI_MODEL_NAME = "gemini-2.0-flash-lite-001"

# Google Docs APIの設定
# ランタイムサービスアカウントを使用するため、キーファイルは不要
SCOPES = ['https://www.googleapis.com/auth/documents', 'https://www.googleapis.com/auth/drive.file'] # Driveへのファイル作成権限も必要

# Flask
app = Flask(__name__)

# Init Gemini Client
vertexai.init(project=GCP_PROJECT_ID, location=GCP_REGION)
aiplatform.init(project=GCP_PROJECT_ID, location=GCP_REGION)
gemini_model = aiplatform.gapic.PredictionServiceClient(client_options={"api_endpoint": f"{GCP_REGION}-aiplatform.googleapis.com"})

# --- Google Docs クライアント初期化 ---
try:
    # Cloud Run環境では、ランタイムサービスアカウントのデフォルト認証情報を使用
    credentials, project = default(scopes=SCOPES)
    docs_service = build('docs', 'v1', credentials=credentials)
    drive_service = build('drive', 'v3', credentials=credentials) # ドキュメント作成にDrive APIも使用
except Exception as e:
    print(f"Error initializing Google API clients: {e}")
    docs_service = None
    drive_service = None

# --- ヘルパー関数 ---
def get_page_title(url):
    """指定されたURLからページのタイトルを取得する"""
    try:
        # requests ライブラリが存在する場合のみ実行
        import requests
        from bs4 import BeautifulSoup
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status() # エラーがあれば例外を発生
        soup = BeautifulSoup(response.content, 'html.parser')
        title = soup.title.string if soup.title else "タイトル不明"
        return title.strip()
    except Exception as e:
        print(f"Error fetching page title for {url}: {e}")
        return "タイトル取得失敗"

def call_gemini(text_content, url):
    """Gemini APIを呼び出して、和訳、整形、名詞抽出を行う"""
    model = GenerativeModel(GEMINI_MODEL_NAME)
    prompt = f"""以下のテキストを指定の形式で処理してください。

# 元のテキスト:
```
{text_content}
```

# 元のURL:
{url}

# 処理指示:
1.  元のテキストを日本語に自然に和訳してください。
2.  和訳した内容は、重要なポイントを箇条書き（各項目の先頭は「・」）でまとめてください。
3.  元のテキストに含まれるコードブロック（```で囲まれた部分）は、内容を保持し、コードブロックとしてわかるように ``` で囲んでください。
4.  元のURLからWebページのタイトルを取得してください。
5.  和訳した文章の内容と元のテキストのテーマに最も関連性の高い重要な名詞を10個程度、カンマ区切りでリストアップしてください。固有名詞や専門用語を優先してください。

# 出力形式 (JSON):
```json
{{
  "translated_summary": "・箇条書き1\n・箇条書き2\n・箇条書き3...",
  "code_blocks": [
    "```python\nprint('hello')\n```",
    "```javascript\nconsole.log('world');\n```"
  ],
  "page_title": "取得したページタイトル",
  "keywords": "名詞1, 名詞2, 名詞3, ..."
}}
```

上記形式に従って、JSON文字列のみを出力してください。
"""

    # 生成設定 (任意)
    generation_config = GenerationConfig(
        temperature=0.5,
        max_output_tokens=2048,
        top_k=40,
        top_p=0.9
    )

    try:
        # テキスト生成を実行
        response = model.generate_content(
            [Part.from_text(prompt)],
            generation_config=generation_config,
            # safety_settings=... # 必要に応じてセーフティ設定を追加
        )

        # レスポンスからテキスト部分を取得
        prediction = response.text # response.candidates[0].content.parts[0].text の方が確実な場合あり

        # JSONパースを試みる (Geminiの出力が常に期待通りとは限らないため注意)
        try:
            # ```json ... ``` を除去 (念のため)
            cleaned_prediction = re.sub(r'^```json\s*', '', prediction.strip())
            cleaned_prediction = re.sub(r'\s*```$', '', cleaned_prediction)
            result = json.loads(cleaned_prediction)
            return result
        except json.JSONDecodeError as e:
            print(f"Error decoding Gemini JSON response: {e}")
            print(f"Raw Gemini response: {prediction}")
            # フォールバック: パース失敗時はエラー情報を含む辞書を返す
            return {
                "translated_summary": f"Gemini応答のJSON解析失敗: {e}\nRaw: {prediction}",
                "code_blocks": [],
                "page_title": "取得失敗",
                "keywords": "抽出失敗"
            }
    except Exception as e:
        print(f"Error calling Gemini API via GenerativeModel: {e}")
        import traceback
        traceback.print_exc()
        return {
            "translated_summary": f"Gemini API呼び出しエラー: {e}",
            "code_blocks": [],
            "page_title": "取得失敗",
            "keywords": "抽出失敗"
        }

def create_google_doc(title, content_requests):
    """Googleドキュメントを作成し、指定された内容とスタイルで書き込む"""
    if not docs_service or not drive_service:
        raise Exception("Google Docs or Drive service not initialized.")

    try:
        # 1. ドキュメントを作成 (Drive APIを使用)
        doc_body = {
            'name': title,
            'mimeType': 'application/vnd.google-apps.document'
        }
        created_doc = drive_service.files().create(body=doc_body, fields='id,webViewLink').execute()
        permission = {
            'type': 'user',
            'role': 'reader',
            'emailAddress': 'shougoss90@gmail.com'
        }
        document_id = created_doc.get('id')
        try:
            drive_service.permissions().create(
                fileId=document_id,
                body=permission,
                fields='id'
            ).execute()
            print(f"Shared document {document_id} with admin")
        except Exception as share_e:
            print(f"Error sharing document {document_id}: {share_e}")
            
        doc_url = created_doc.get('webViewLink')

        if not document_id:
             raise Exception("Failed to create Google Doc, no ID returned.")

        # 2. ドキュメントに内容を書き込む (Docs APIを使用)
        result = docs_service.documents().batchUpdate(
            documentId=document_id,
            body={'requests': content_requests}
        ).execute()

        print(f"Google Doc created: {doc_url}")
        return doc_url

    except Exception as e:
        print(f"Error interacting with Google Docs/Drive API: {e}")
        # エラーが発生した場合、作成途中のドキュメントを削除する試み (オプション)
        if 'document_id' in locals() and document_id:
            try:
                drive_service.files().delete(fileId=document_id).execute()
                print(f"Cleaned up partially created document: {document_id}")
            except Exception as delete_e:
                print(f"Error deleting partially created document {document_id}: {delete_e}")
        raise

def format_docs_requests(gemini_result, url):
    """Geminiの結果をGoogle Docs APIのリクエスト形式に変換する"""
    requests_list = []
    current_index = 1  # Docs APIの挿入位置

    # --- 1. Summary ---
    requests_list.append({
        'insertText': {
            'location': {'index': current_index},
            'text': "1 - Summary\n"
        }
    })
    current_index += len("1 - Summary\n")
    requests_list.append({ # 見出しスタイルを適用 (例: HEADING_1)
        'updateParagraphStyle': {
            'range': {'startIndex': 1, 'endIndex': current_index},
            'paragraphStyle': {'namedStyleType': 'HEADING_1'},
            'fields': 'namedStyleType'
        }
    })

    summary_lines = gemini_result.get("translated_summary", "和訳の取得に失敗しました。").split('\n')
    for line in summary_lines:
        if line.strip():
            # 箇条書きの「・」を維持しつつ挿入
            line_text = line + "\n"
            requests_list.append({
                'insertText': {
                    'location': {'index': current_index},
                    'text': line_text
                }
            })
            line_start_index = current_index
            current_index += len(line_text)

            # 箇条書きスタイルを適用 (行頭が「・」の場合)
            if line.strip().startswith('・'):
                 requests_list.append({
                    'createParagraphBullets': {
                        'range': {
                            'startIndex': line_start_index,
                            'endIndex': current_index -1 # 改行文字を含めない
                        },
                        'bulletPreset': 'BULLET_DISC_CIRCLE_SQUARE' # 例: 標準的な黒丸
                    }
                 })

    # --- Code block ---
    code_blocks = gemini_result.get("code_blocks", [])
    if code_blocks:
         requests_list.append({
             'insertText': {
                 'location': {'index': current_index},
                 'text': "\nCode block:\n" # Code blockセクションのタイトル
             }
         })
         current_index += len("\nCode block:\n")

         for code_block in code_blocks:
            code_text = code_block + "\n\n" # Code blockの後には空行を入れる
            requests_list.append({
                'insertText': {
                    'location': {'index': current_index},
                    'text': code_text
                }
            })
            code_start_index = current_index
            current_index += len(code_text)

            # コードブロック部分に等幅フォントスタイルを適用
            requests_list.append({
                'updateTextStyle': {
                    'range': {'startIndex': code_start_index, 'endIndex': current_index - 2}, # 最後の改行2つ分を除外
                    'textStyle': {
                        'weightedFontFamily': {
                            'fontFamily': 'Courier New' # または 'Consolas' など
                        },
                        'fontSize': {'magnitude': 10, 'unit': 'PT'} # フォントサイズ調整
                    },
                    'fields': 'weightedFontFamily,fontSize'
                }
            })

    # --- 2. URLとページ名 ---
    page_title = gemini_result.get("page_title", "タイトル取得失敗")
    url_text = f"\n2 - URL - {page_title}\n{url}\n"
    requests_list.append({
        'insertText': {
            'location': {'index': current_index},
            'text': url_text
        }
    })
    url_title_start_index = current_index + 1 # 先頭の改行分
    current_index += len(url_text)
    requests_list.append({ # 見出しスタイルを適用 (例: HEADING_2)
        'updateParagraphStyle': {
            'range': {'startIndex': url_title_start_index, 'endIndex': url_title_start_index + len(f"2 - URL - {page_title}")},
            'paragraphStyle': {'namedStyleType': 'HEADING_2'},
            'fields': 'namedStyleType'
        }
    })
    # URL部分にリンクスタイルを適用
    requests_list.append({
        'updateTextStyle': {
            'range': {'startIndex': current_index - len(url) -1 , 'endIndex': current_index -1},
            'textStyle': {
                'link': {'url': url}
            },
            'fields': 'link'
        }
    })


    # --- 3. 名詞リスト ---
    keywords = gemini_result.get("keywords", "抽出失敗")
    keywords_text = f"\n3 - 関連キーワード\n{keywords}\n"
    requests_list.append({
        'insertText': {
            'location': {'index': current_index},
            'text': keywords_text
        }
    })
    keywords_title_start_index = current_index + 1 # 先頭の改行分
    current_index += len(keywords_text)
    requests_list.append({ # 見出しスタイルを適用 (例: HEADING_2)
        'updateParagraphStyle': {
            'range': {'startIndex': keywords_title_start_index, 'endIndex': keywords_title_start_index + len("3 - 関連キーワード")},
            'paragraphStyle': {'namedStyleType': 'HEADING_2'},
            'fields': 'namedStyleType'
        }
    })


    return requests_list


# main
@app.route('/', methods=['POST'])
def process_text():
    """HTTP POSTリクエストを受け取り、テキスト処理を実行してDocs URLを返す"""
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    text_content = data.get('text')
    url = data.get('url')

    if not text_content or not url:
        return jsonify({"error": "Missing 'text' or 'url' in request body"}), 400

    if not GCP_PROJECT_ID:
         return jsonify({"error": "GCP_PROJECT_ID environment variable not set."}), 500

    if not docs_service or not drive_service:
         return jsonify({"error": "Google API services failed to initialize. Check logs."}), 500

    try:
        # 1. Geminiで処理
        print(f"Calling Gemini for URL: {url}")
        gemini_result = call_gemini(text_content, url)
        print("Gemini processing complete.")

        # 2. Googleドキュメント作成と書き込み
        # ドキュメントタイトルを設定 (ページタイトル + 最初の数文字)
        doc_title = f"{gemini_result.get('page_title', 'No Title')} - {text_content[:20]}..."
        print(f"Creating Google Doc with title: {doc_title}")

        # Docs APIリクエストを作成
        content_requests = format_docs_requests(gemini_result, url)

        # Googleドキュメントを作成・書き込み
        doc_url = create_google_doc(doc_title, content_requests)
        print(f"Successfully created Google Doc: {doc_url}")

        # 3. GoogleドキュメントのURLを返す
        return jsonify({"document_url": doc_url})

    except aiplatform.exceptions.PermissionDenied as e:
        print(f"Vertex AI Permission Denied: {e}")
        return jsonify({"error": f"Vertex AI Permission Denied. Ensure the service account has 'Vertex AI User' role. Details: {e}"}), 500
    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"An internal server error occurred: {e}"}), 500

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

