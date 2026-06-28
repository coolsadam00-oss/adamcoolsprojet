# Adam Games Android App

This folder contains a Google Play-ready Android wrapper for the website at:

```text
https://www.gexora.onrender.com/
```

It uses a native Android WebView with JavaScript, cookies, file uploads, downloads, back-button navigation, and a small offline screen.

## Requirements

- Android Studio with JDK 17
- Android SDK Platform 35 installed
- A live HTTPS website. Google Play will review the app against the live site.

Google Play currently requires new Android phone/tablet app submissions to target Android 15 / API level 35 or higher.

## Change the Website URL

Edit this line in `app/build.gradle` if your website URL changes:

```gradle
buildConfigField "String", "BASE_URL", "\"https://www.gexora.onrender.com/\""
```

## Build a Debug App

Open this `android_app` folder in Android Studio, let Gradle sync, then use:

```text
Build > Build Bundle(s) / APK(s) > Build APK(s)
```

If you have Gradle or a generated Gradle wrapper available, you can also run:

```powershell
.\gradlew.bat assembleDebug
```

The debug APK will be created under:

```text
app/build/outputs/apk/debug/
```

## Build a Play Store AAB

Create a release keystore:

```powershell
keytool -genkeypair -v -keystore adam-games-release.jks -keyalg RSA -keysize 2048 -validity 10000 -alias adam-games
```

Create `keystore.properties` in this folder:

```properties
storeFile=adam-games-release.jks
storePassword=YOUR_STORE_PASSWORD
keyAlias=adam-games
keyPassword=YOUR_KEY_PASSWORD
```

Then build the Android App Bundle:

```text
Build > Generate Signed App Bundle / APK > Android App Bundle
```

If you have Gradle or a generated Gradle wrapper available, you can also run:

```powershell
.\gradlew.bat bundleRelease
```

Upload this file in Google Play Console:

```text
app/build/outputs/bundle/release/app-release.aab
```

## Play Store Checklist

- App name: Adam Games
- Package name: `com.adamcoolsprojet.games`
- Privacy policy URL: `https://www.gexora.onrender.com/privacy`
- Terms/rules URL: `https://www.gexora.onrender.com/terms`
- Target SDK: 35
- Content rating: choose based on the games you allow users to upload
- Data safety: declare account info and user-generated content if the public site allows uploads, comments, ratings, or logins

For a stricter Play Store setup later, consider switching from WebView to Trusted Web Activity and adding Digital Asset Links on the website.
