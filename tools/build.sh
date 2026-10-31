#!/usr/bin/env bash
# Builds the Rainy watch face from watchface/src/main without Gradle (aapt2 + the debug key, a few seconds),
# installs it on a watch and makes it the active face. The release build for Google Play is Gradle's
# (watchface/build.gradle.kts, README.md); this is for iterating on the face.
#
# Usage: tools/build.sh [--serial emulator-5554] [--generate] [--pin "it 24 14:54"] [--no-install] [--preview]
#                                 [--validate]
#   --generate   run tools/generate.py first
#   --pin        generate the face with a fixed language, 12/24-hour mode and time (generate.py --pin), to look at one
#                case on the watch; run generate.py again before committing
#   --preview    make the editor preview (res/drawable-nodpi/preview.png): the face at 10:08 in English with sample
#                data in the slots, screenshotted without the system's charging indicator. An emulator with a host
#                keyboard keeps a notification dot at the bottom ("physical keyboard"): paint it out (the face is
#                black there) or use an AVD with hw.keyboard=no
#   --validate   run Google's Watch Face Push validator on the APK (needs $WFF_VALIDATOR_JAR, a
#                validator-push-cli jar from github.com/google/watchface, and Java 17+). Its "Package name" check is
#                for pushed faces only and always fails here; `./gradlew :watchface:validateDebugWatchFace` ignores it
# The package and SDK levels are read from watchface/build.gradle.kts, the version from gradle.properties.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/watchface/src/main"
OUT="$ROOT/watchface/build/aapt2"
GRADLE="$ROOT/watchface/build.gradle.kts"
SERIAL=emulator-5554 GENERATE=0 INSTALL=1 PREVIEW=0 VALIDATE=0 PIN=
while [ $# -gt 0 ]; do
    case "$1" in
        --serial) SERIAL="$2"; shift ;;
        --generate) GENERATE=1 ;;
        --pin) PIN="$2"; shift ;;
        --no-install) INSTALL=0 ;;
        --preview) PREVIEW=1 ;;
        --validate) VALIDATE=1 ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
    shift
done

gradle_value() { grep -oE "^\s*$1\s*=\s*\"?[^\"]+\"?" "$GRADLE" | head -1 | sed -E 's/.*=\s*"?([^"]+)"?/\1/'; }
PKG="$(gradle_value applicationId)"
MIN_SDK="$(gradle_value minSdk)"
TARGET_SDK="$(gradle_value targetSdk)"
VERSION_CODE="$(sed -n 's/^face.versionCode=//p' "$ROOT/gradle.properties")"
VERSION_NAME="$(sed -n 's/^face.versionName=//p' "$ROOT/gradle.properties")"
: "${PKG:?no applicationId in $GRADLE}"

if [ "$PREVIEW" = 1 ]; then
    python3 "$ROOT/tools/generate.py" --pin "en 24 10:08" --sample
elif [ -n "$PIN" ]; then
    python3 "$ROOT/tools/generate.py" --pin "$PIN"
elif [ "$GENERATE" = 1 ]; then
    python3 "$ROOT/tools/generate.py"
fi

SDK="${ANDROID_HOME:-$HOME/Android/Sdk}"
BT="$SDK/build-tools/$(ls "$SDK/build-tools" | sort -V | tail -1)"
JAR="$SDK/platforms/$(ls "$SDK/platforms" | sort -V | tail -1)/android.jar"
ADB="$SDK/platform-tools/adb -s $SERIAL"
APK="$OUT/rainy-watchface-debug.apk"
rm -rf "$OUT" && mkdir -p "$OUT"
if [ ! -f "$SRC/res/drawable-nodpi/preview.png" ]; then
    # The first build: a black placeholder until --preview saves a screenshot.
    mkdir -p "$SRC/res/drawable-nodpi"
    python3 -c 'import sys, zlib, struct
def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
raw = b"".join(b"\0" + b"\0" * 450 for _ in range(450))
sys.stdout.buffer.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 450, 450, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))' \
        > "$SRC/res/drawable-nodpi/preview.png"
    echo "No preview yet: wrote a black placeholder (run with --preview)"
fi
# The source manifest has no package (Gradle sets it): add it for aapt2.
sed -E "s#<manifest #<manifest package=\"$PKG\" #" "$SRC/AndroidManifest.xml" > "$OUT/AndroidManifest.xml"
"$BT/aapt2" compile --dir "$SRC/res" -o "$OUT/res.zip"
"$BT/aapt2" link -o "$OUT/unaligned.apk" -I "$JAR" --manifest "$OUT/AndroidManifest.xml" \
    --min-sdk-version "$MIN_SDK" --target-sdk-version "$TARGET_SDK" \
    --version-code "$VERSION_CODE" --version-name "$VERSION_NAME" "$OUT/res.zip"
"$BT/zipalign" -f 4 "$OUT/unaligned.apk" "$OUT/aligned.apk"
"$BT/apksigner" sign --ks "$HOME/.android/debug.keystore" --ks-pass pass:android --out "$APK" "$OUT/aligned.apk"
rm -f "$APK.idsig"
echo "Built $APK ($PKG $VERSION_NAME, $(($(stat -c %s "$APK") / 1024)) KB)"

if [ "$VALIDATE" = 1 ]; then
    : "${WFF_VALIDATOR_JAR:?set WFF_VALIDATOR_JAR to a validator-push-cli jar}"
    JAVA="${JAVA_HOME:-$HOME/.jdks/openjdk-26.0.1}/bin/java"
    (cd "$OUT" && "$JAVA" -jar "$WFF_VALIDATOR_JAR" --apk_path="$APK" --package_name=app.wakirox.rainy) | grep -v "generated token" || true
fi

[ "$INSTALL" = 1 ] || exit 0
if ! out="$($ADB install -r "$APK" 2>&1)"; then
    if grep -q -E "INSTALL_FAILED_(UPDATE_INCOMPATIBLE|VERSION_DOWNGRADE)" <<<"$out"; then
        # Installed from Play or a release build (another key), or a newer version: replace it.
        echo "Replacing the installed $PKG (another key or a newer version)"
        $ADB uninstall "$PKG" >/dev/null
        $ADB install "$APK"
    else
        echo "$out" >&2; exit 1
    fi
fi
# A face that crashed the renderer twice makes the system hold the runtime off for 90 minutes: restart it.
$ADB shell am force-stop com.google.wear.watchface.runtime
$ADB shell am broadcast -a com.google.android.wearable.app.DEBUG_SURFACE --es operation set-watchface --es watchFaceId "$PKG" >/dev/null
echo "Installed and set as the face on $SERIAL"

if [ "$PREVIEW" = 1 ]; then
    # No charging bolt.
    $ADB shell dumpsys battery unplug
    sleep 6
    # The screenshot can show the previous frame until the face is brought to the front again.
    $ADB shell input keyevent KEYCODE_BACK
    $ADB shell am start -a android.intent.action.MAIN -c android.intent.category.HOME >/dev/null 2>&1 || true
    sleep 3
    mkdir -p "$SRC/res/drawable-nodpi"
    $ADB exec-out screencap -p > "$SRC/res/drawable-nodpi/preview.png"
    $ADB shell dumpsys battery reset
    python3 "$ROOT/tools/generate.py" >/dev/null
    echo "Saved the preview: watchface/src/main/res/drawable-nodpi/preview.png (and regenerated the face)"
fi
