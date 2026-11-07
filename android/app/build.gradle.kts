plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.lavzemkhangi.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.lavzemkhangi.app"
        minSdk = 23
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
    }

    val signingStoreFile = System.getenv("ANDROID_KEYSTORE_PATH")
    val signingStorePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
    val signingKeyAlias = System.getenv("ANDROID_KEY_ALIAS")
    val signingKeyPassword = System.getenv("ANDROID_KEY_PASSWORD")

    signingConfigs {
        create("release") {
            if (!signingStoreFile.isNullOrBlank() &&
                !signingStorePassword.isNullOrBlank() &&
                !signingKeyAlias.isNullOrBlank() &&
                !signingKeyPassword.isNullOrBlank()
            ) {
                storeFile = file(signingStoreFile)
                storePassword = signingStorePassword
                keyAlias = signingKeyAlias
                keyPassword = signingKeyPassword
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (!signingStoreFile.isNullOrBlank() &&
                !signingStorePassword.isNullOrBlank() &&
                !signingKeyAlias.isNullOrBlank() &&
                !signingKeyPassword.isNullOrBlank()
            ) {
                signingConfig = signingConfigs.getByName("release")
            }
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
