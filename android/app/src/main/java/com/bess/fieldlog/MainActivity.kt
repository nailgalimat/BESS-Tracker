package com.bess.fieldlog

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.MediaStore
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.webkit.WebViewAssetLoader
import java.io.File

/**
 * Offline-first wrapper around the BESS Field Log web app.
 *
 * The whole UI (backend/static, copied into assets/app at build time) is
 * served from inside the APK via WebViewAssetLoader, so the app opens with
 * no network at all. Entries and photos are stored by the web app in
 * IndexedDB / localStorage on the device; when the phone can reach the
 * FastAPI server (LAN), the built-in Sync pushes the queue — same engine the
 * PWA uses. The server address is entered on the login screen and kept in
 * localStorage, so nothing here hardcodes it.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private var filePathCallback: ValueCallback<Array<Uri>>? = null
    private var cameraPhotoUri: Uri? = null

    private val fileChooserLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            val cb = filePathCallback ?: return@registerForActivityResult
            filePathCallback = null
            if (result.resultCode != Activity.RESULT_OK) {
                cb.onReceiveValue(null); return@registerForActivityResult
            }
            val data = result.data
            val uris = when {
                // Camera capture: no data intent, the photo landed in our URI
                data == null || data.data == null && data.clipData == null ->
                    cameraPhotoUri?.let { arrayOf(it) }
                data.clipData != null -> {
                    val clip = data.clipData!!
                    Array(clip.itemCount) { i -> clip.getItemAt(i).uri }
                }
                else -> arrayOf(data.data!!)
            }
            cb.onReceiveValue(uris)
            cameraPhotoUri = null
        }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        webView = WebView(this)
        setContentView(webView)

        val assetLoader = WebViewAssetLoader.Builder()
            .addPathHandler("/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true          // localStorage + IndexedDB
            databaseEnabled = true
            allowFileAccess = false
            allowContentAccess = true
            mediaPlaybackRequiresUserGesture = true
            // The UI is served over https://appassets.androidplatform.net but
            // the sync API is plain http on the LAN (192.168.137.1). Without
            // this the WebView blocks those requests as mixed content and the
            // app reports "cannot reach server".
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        }

        webView.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(
                view: WebView, request: WebResourceRequest,
            ): WebResourceResponse? = assetLoader.shouldInterceptRequest(request.url)

            override fun shouldOverrideUrlLoading(
                view: WebView, request: WebResourceRequest,
            ): Boolean {
                // Keep the app inside the WebView: our own asset pages and the
                // API host load normally; anything else opens externally.
                val host = request.url.host ?: return false
                return if (host == WebViewAssetLoader.DEFAULT_DOMAIN) false else {
                    startActivity(Intent(Intent.ACTION_VIEW, request.url)); true
                }
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView,
                callback: ValueCallback<Array<Uri>>,
                params: FileChooserParams,
            ): Boolean {
                filePathCallback?.onReceiveValue(null)
                filePathCallback = callback

                // Direct-camera intent writing into our FileProvider cache
                val photoDir = File(cacheDir, "camera").apply { mkdirs() }
                val photoFile = File(photoDir, "photo_${System.currentTimeMillis()}.jpg")
                cameraPhotoUri = FileProvider.getUriForFile(
                    this@MainActivity, "com.bess.fieldlog.fileprovider", photoFile)
                val camera = Intent(MediaStore.ACTION_IMAGE_CAPTURE).apply {
                    putExtra(MediaStore.EXTRA_OUTPUT, cameraPhotoUri)
                }

                val gallery = Intent(Intent.ACTION_GET_CONTENT).apply {
                    type = "image/*"
                    addCategory(Intent.CATEGORY_OPENABLE)
                    putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                }

                val chooser = Intent.createChooser(gallery, "Photo").apply {
                    putExtra(Intent.EXTRA_INITIAL_INTENTS, arrayOf(camera))
                }
                return try {
                    fileChooserLauncher.launch(chooser); true
                } catch (e: Exception) {
                    filePathCallback = null; false
                }
            }
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) webView.goBack() else finish()
            }
        })

        if (savedInstanceState == null) {
            webView.loadUrl("https://${WebViewAssetLoader.DEFAULT_DOMAIN}/app/index.html")
        } else {
            webView.restoreState(savedInstanceState)
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
    }
}
