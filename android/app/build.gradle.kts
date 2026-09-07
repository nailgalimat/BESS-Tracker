plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.bess.fieldlog"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.bess.fieldlog"
        minSdk = 26
        targetSdk = 34
        versionCode = 3
        versionName = "1.2"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.11.0")
}

// The web app lives in backend/static (single source of truth — same files the
// FastAPI server serves as the PWA). Copy it into the APK assets on every
// build so the app always ships the current UI.
val syncWebAssets = tasks.register<Copy>("syncWebAssets") {
    from(rootProject.file("../backend/static"))
    into(layout.projectDirectory.dir("src/main/assets/app"))
    exclude("sw.js")   // service worker is pointless (and unsupported) in-app
}
tasks.named("preBuild") { dependsOn(syncWebAssets) }
