import os
import time
import datetime
from gtts import gTTS # Neu: gTTS Bibliothek importieren
import numpy as np
import stat
import subprocess
import requests # F�r HTTP-Anfragen
import json     # F�r JSON-Verarbeitung
# pyttsx3, socket und base64 werden f�r diese Version nicht ben�tigt

# --- Konfiguration ---
# Pfad zur Named Pipe (FIFO) im selben Verzeichnis wie das Skript
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FIFO_PATH = os.path.join(SCRIPT_DIR, "zeitansage_audio_fifo")

SAMPLE_RATE = 10000  # Abtastrate in Hz (10 kHz)
BEEP_FREQUENCY = 500 # Frequenz des Beep-Tons in Hz
BEEP_DURATION_SECONDS = 0.2 # Dauer eines einzelnen Beep-Tons

# Zeitansage-Zyklus:
UPDATE_INTERVAL_SECONDS = 30 # Zeitintervall zwischen dem Beginn der Ansagen
COUNTDOWN_BEATS = 5 # Anzahl der Beeps vor der Hauptansage
BEEP_CYCLE_INTERVAL = 1.0 # Zeit zwischen den Beep-Beginnen (1 Sekunde f�r "jede Sekunde")
INTER_LANGUAGE_SILENCE_SECONDS = 0.75 # Pause zwischen den verschiedenen Sprachansagen
INTER_ANNOUNCEMENT_SILENCE_SECONDS = 0.5 # Kurze Pause zwischen Zeit- und Wetteransage

SPEAKER_VOLUME = 0.8 # Lautst�rke der Sprachausgabe (0.0 - 1.0)
BEEP_VOLUME = 0.5 # Lautst�rke des Beep-Tons (0.0 - 1.0)

# gTTS steuert die Sprechgeschwindigkeit nicht direkt �ber eine Rate wie pyttsx3.
# Die gTTS-Bibliothek bietet nur einen 'slow'-Parameter (True/False).
# Anpassungen der Geschwindigkeit m�ssten ggf. �ber FFmpeg erfolgen, falls n�tig.
# Die hier definierten Raten dienen nur als Dokumentation oder f�r zuk�nftige Anpassungen
# mit FFmpeg's atempo-Filter.
SPEAKER_RATE_DE_COMMENT = "Normale Geschwindigkeit f�r Deutsch (gTTS)"
SPEAKER_RATE_EN_COMMENT = "Etwas langsamer f�r Englisch (gTTS)"
SPEAKER_RATE_FR_COMMENT = "Noch langsamer f�r Franz�sisch (gTTS)"

# NEU: Lautst�rkeanpassungen in dB f�r die Sprachsynthese via FFmpeg (positiver Wert = lauter)
VOLUME_GAIN_DE_DB = 6.0 # Beispiel: 6 dB Lautst�rkeerh�hung f�r Deutsch
VOLUME_GAIN_EN_DB = 0.0 # Keine zus�tzliche Lautst�rkeerh�hung f�r Englisch
VOLUME_GAIN_FR_DB = 0.0 # Keine zus�tzliche Lautst�rkeerh�hung f�r Franz�sisch (falls reaktiviert)


# Wetter-API-Endpunkt
WEATHER_API_URL = "http://kremser-digital.duckdns.org/data" # Adresse f�r die JSON-Daten
WEATHER_FETCH_TIMEOUT = 5 # Timeout in Sekunden f�r den API-Aufruf

# --- Hilfsfunktionen ---

def generate_beep_wave(freq, duration, sample_rate, volume):
    """Generiert einen Sinuswellen-Beep (Float32)."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    beep_wave = (volume * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return beep_wave

def generate_silence_wave(duration_seconds, sample_rate):
    """Generiert ein Array von Stille (Float32)."""
    return np.zeros(int(duration_seconds * sample_rate), dtype=np.float32)

# Funktion zur Generierung von TTS-Audiodateien mit gTTS
def generate_gtts_audio_file(text, output_path, lang_code, slow_speed=False):
    """
    Generiert eine MP3-Audio-Datei aus Text mit gTTS.
    Die Geschwindigkeit kann mit 'slow_speed=True' auf 0.5x reduziert werden.
    """
    try:
        # gTTS speichert direkt eine MP3-Datei
        tts = gTTS(text=text, lang=lang_code, slow=slow_speed)
        tts.save(output_path)
        print(f"INFO: gTTS-Audio f�r '{lang_code}' erfolgreich gespeichert in '{output_path}'.")
        return True
    except Exception as e:
        print(f"FEHLER: gTTS konnte Audio nicht generieren oder speichern f�r Text '{text[:50]}...': {e}")
        return False

def convert_audio_to_float32_mono_10khz(input_audio_path, target_sample_rate, volume_db=0.0):
    """
    Konvertiert eine Audio-Datei (z.B. MP3 von gTTS) zu einem NumPy float32 Array (mono, target_sample_rate)
    mithilfe von FFmpeg und gibt es zur�ck.
    FFmpeg erkennt das Eingabeformat automatisch.
    F�gt optional einen Lautst�rke-Filter hinzu.
    """
    ffmpeg_command = [
        'ffmpeg',
        '-i', input_audio_path,
        '-f', 'f32le',               # Ausgabeformat: 32-bit float, little-endian
        '-acodec', 'pcm_f32le',      # Audio-Codec: PCM 32-bit float, little-endian
        '-ar', str(target_sample_rate), # Audio-Abtastrate (ziel: 10 kHz)
        '-ac', '1',                  # Audio-Kan�le (mono)
        '-map_metadata', '-1',       # Keine Metadaten kopieren
        '-loglevel', 'error',        # Nur Fehler ausgeben
    ]
    
    # NEU: FFmpeg-Lautst�rke-Filter hinzuf�gen
    if volume_db != 0.0:
        ffmpeg_command.extend(['-af', f"volume={volume_db}dB"])

    ffmpeg_command.append('-') # Ausgabe an stdout
    
    try:
        process = subprocess.run(ffmpeg_command, check=True, capture_output=True)
        audio_data = np.frombuffer(process.stdout, dtype=np.float32)
        return audio_data
    except subprocess.CalledProcessError as e: # Korrigierter Fehlername
        print(f"FEHLER: FFmpeg-Prozess fehlgeschlagen bei Konvertierung von '{input_audio_path}'.")
        print(f"FFmpeg stdout: {e.stdout.decode()}")
        print(f"FFmpeg stderr: {e.stderr.decode()}")
        return None
    except Exception as e:
        print(f"Ein unerwarteter Fehler ist aufgetreten w�hrend der FFmpeg-Konvertierung: {e}")
        return None

def fetch_weather_data(url, timeout):
    """Ruft Wetterdaten von einer URL ab und parst sie als JSON."""
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return json.loads(response.text)
    except requests.exceptions.Timeout:
        print(f"FEHLER: Timeout beim Abrufen von Wetterdaten von {url}")
        return None
    except requests.exceptions.ConnectionError:
        print(f"FEHLER: Verbindungsfehler beim Abrufen von Wetterdaten von {url}. Ist der Server erreichbar?")
        return None
    except requests.exceptions.HTTPError as e:
        print(f"FEHLER: HTTP-Fehler {e.response.status_code} beim Abrufen von Wetterdaten von {url}")
        return None
    except json.JSONDecodeError:
        print(f"FEHLER: Konnte JSON von {url} nicht parsen. Ung�ltiges JSON-Format. Raw-Daten: '{response.text[:100]}...'")
        return None
    except Exception as e:
        print(f"Ein unerwarteter Fehler ist beim Abrufen/Parsen von Wetterdaten aufgetreten: {e}")
        return None

# --- Haupt-Streaming-Funktion ---

def main():
    # Pr�fe Abh�ngigkeiten
    try:
        subprocess.run(['ffmpeg', '-version'], check=True, capture_output=True)
        print("FFmpeg gefunden. Wird f�r Audioverarbeitung verwendet.")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("FEHLER: FFmpeg ist nicht installiert oder nicht im PATH gefunden.")
        print("Bitte installieren Sie FFmpeg (z.B. 'sudo apt install ffmpeg' unter Debian/Ubuntu).")
        return
    try:
        import requests
        print("Requests Bibliothek gefunden. Wird f�r HTTP-Anfragen verwendet.")
    except ImportError:
        print("FEHLER: 'requests' Bibliothek nicht gefunden. Bitte installieren Sie sie ('pip install requests').")
        return
    try:
        from gtts import gTTS
        print("gTTS Bibliothek gefunden. Wird f�r Text-zu-Sprache verwendet.")
    except ImportError:
        print("FEHLER: 'gtts' Bibliothek nicht gefunden. Bitte installieren Sie sie ('pip install gtts').")
        return
    
    # FIFO erstellen und Berechtigungen pr�fen
    if os.path.exists(FIFO_PATH):
        if not stat.S_ISFIFO(os.stat(FIFO_PATH).st_mode):
            print(f"FEHLER: '{FIFO_PATH}' existiert, ist aber keine Named Pipe. Bitte l�schen oder umbenennen.")
            return
    else:
        try:
            os.mkfifo(FIFO_PATH)
            print(f"Named Pipe (FIFO) '{FIFO_PATH}' erstellt.")
        except OSError as e:
            print(f"FEHLER: Konnte Named Pipe '{FIFO_PATH}' nicht erstellen: {e}")
            print("Stellen Sie sicher, dass Sie die Berechtigung haben, in diesem Verzeichnis zu schreiben.")
            return

    print(f"Schreibe Audio-Stream als float32 in Named Pipe '{FIFO_PATH}' mit {SAMPLE_RATE} Hz...")
    print("Starte einen Reader (z.B. VLC oder GNU Radio) aus dieser Pipe.")

    beep_wave = generate_beep_wave(BEEP_FREQUENCY, BEEP_DURATION_SECONDS, SAMPLE_RATE, BEEP_VOLUME)
    silence_after_beep_duration = BEEP_CYCLE_INTERVAL - BEEP_DURATION_SECONDS
    silence_after_beep_wave = generate_silence_wave(silence_after_beep_duration, SAMPLE_RATE)
    pulsed_beep_segment = np.concatenate((beep_wave, silence_after_beep_wave))

    inter_lang_silence_wave = generate_silence_wave(INTER_LANGUAGE_SILENCE_SECONDS, SAMPLE_RATE)
    inter_announcement_silence_wave = generate_silence_wave(INTER_ANNOUNCEMENT_SILENCE_SECONDS, SAMPLE_RATE)

    # Der Pfad f�r die tempor�re MP3-Datei im Skript-Verzeichnis
    tts_audio_file_path = os.path.join(SCRIPT_DIR, "zeitansage_temp_tts.mp3") # .mp3 statt .wav

    next_update_time = time.monotonic() # Zeitpunkt f�r den Beginn des n�chsten Update-Zyklus

    while True:
        try:
            # �ffne die FIFO im Bin�r-Schreibmodus. Dies blockiert, bis ein Reader die Pipe �ffnet.
            with open(FIFO_PATH, 'wb') as fifo_file:
                print(f"FIFO '{FIFO_PATH}' ge�ffnet. Warte auf Reader...")
                
                while True: # Innere Schleife, die l�uft, solange der Reader verbunden ist
                    time_until_next_cycle = next_update_time - time.monotonic()
                    if time_until_next_cycle > 0:
                        print(f"Warte {time_until_next_cycle:.2f} Sekunden bis zum n�chsten Ansage-Zyklus...")
                        silence_duration = time_until_next_cycle
                        block_size_samples = int(SAMPLE_RATE * 0.1) # 100ms Bl�cke f�r Stille
                        total_samples_written = 0
                        silence_samples = int(silence_duration * SAMPLE_RATE)
                        
                        while total_samples_written < silence_samples:
                            current_block_samples = min(block_size_samples, silence_samples - total_samples_written)
                            current_silence_block = generate_silence_wave(current_block_samples / SAMPLE_RATE, SAMPLE_RATE)
                            try:
                                fifo_file.write(current_silence_block.tobytes())
                                fifo_file.flush()
                                total_samples_written += current_block_samples
                            except BrokenPipeError:
                                print(f"BrokenPipeError w�hrend Stille: Reader von '{FIFO_PATH}' hat Verbindung getrennt. Warte auf neuen Reader...")
                                break 
                        if total_samples_written < silence_samples:
                            break # Break occurred during silence writing

                    # Starte den Countdown
                    print(f"Beginne {COUNTDOWN_BEATS}-Sekunden-Countdown...")
                    for i in range(COUNTDOWN_BEATS, 0, -1):
                        print(f"Countdown: {i}...")
                        try:
                            fifo_file.write(pulsed_beep_segment.tobytes())
                            fifo_file.flush()
                        except BrokenPipeError:
                            print(f"BrokenPipeError w�hrend Countdown: Reader von '{FIFO_PATH}' hat Verbindung getrennt. Warte auf neuen Reader...")
                            break
                    else: # Only runs if loop completed without break
                        i = 0 
                    if i != 0: # If countdown loop was broken
                        break # Break out of inner while loop as well


                    # --- Wetterdaten abrufen ---
                    weather_data = fetch_weather_data(WEATHER_API_URL, WEATHER_FETCH_TIMEOUT)
                    german_weather_text = "Wetter nicht verf�gbar."
                    english_weather_text = "Weather not available."
                    
                    if weather_data:
                        try:
                            temp_c = weather_data.get("temperatureC")
                            temp_f = weather_data.get("temperatureF")
                            humid  = weather_data.get("humidity")
                            preas  = weather_data.get("pressure")
                            wind_speed = weather_data.get("windSpeed") # NEU: Windgeschwindigkeit auslesen

                            if temp_c is not None:
                                german_weather_text = f"Die Temperatur betraegt {temp_c:.0f} Grad Celsius."
                                if humid is not None:
                                    german_weather_text += f" Die Luftfeuchtigkeit liegt bei {humid:.0f} Prozent."
                                if preas is not None:
                                    german_weather_text += f" Der Luftdruck liegt bei {preas:.0f} Hektopascal."
                                # NEU: Windgeschwindigkeit f�r Deutsch
                                if wind_speed is not None:
                                    if wind_speed <= 0.5:
                                        german_weather_text += " Es ist kein Wind."
                                    else:
                                        german_weather_text += f" Die Windgeschwindigkeit betr�gt {wind_speed:.1f} Meter pro Sekunde."
                            else:
                                german_weather_text = "Temperatur in Celsius nicht gefunden."

                            if temp_f is not None:
                                english_weather_text = f"The temperature is {temp_f:.0f} degrees Fahrenheit."
                                if humid is not None:
                                    english_weather_text += f" The humidity is {humid:.0f} percent."
                                if preas is not None:
                                    english_weather_text += f" The pressure is {preas:.0f} hectopascals."
                                # NEU: Windgeschwindigkeit f�r Englisch
                                if wind_speed is not None:
                                    if wind_speed <= 0.5:
                                        english_weather_text += " There is no wind."
                                    else:
                                        english_weather_text += f" The wind speed is {wind_speed:.1f} meters per second."
                            else:
                                english_weather_text = "Temperature in Fahrenheit not found."
                                
                        except Exception as e:
                            print(f"FEHLER: Problem beim Extrahieren der Wetterdaten aus JSON: {e}")
                            german_weather_text = "Wetterdaten fehlerhaft."
                            english_weather_text = "Weather data faulty."
                    else:
                        print("INFO: Keine Wetterdaten verf�gbar oder Fehler beim Abruf.")

                    # --- Zeit- und Wetteransagen in verschiedenen Sprachen ---
                    now = datetime.datetime.now()
                    
                    # 1. Deutsche Ansage (Zeit)
                    german_time_text = now.strftime("Es ist %H Uhr %M Minuten und %S Sekunden.")
                    print(f"Generiere Deutsch Zeit: {german_time_text}")
                    # gTTS-Synthese und FFmpeg-Konvertierung mit Lautst�rke-Boost
                    tts_success = generate_gtts_audio_file(german_time_text, tts_audio_file_path, 'de', slow_speed=False)
                    if tts_success:
                        tts_audio_data = convert_audio_to_float32_mono_10khz(tts_audio_file_path, SAMPLE_RATE, volume_db=VOLUME_GAIN_DE_DB)
                        if tts_audio_data is None: print("FEHLER bei deutscher Zeitansage (FFmpeg), �berspringe.");
                        else:
                            try: fifo_file.write(tts_audio_data.tobytes()); fifo_file.flush()
                            except BrokenPipeError: print("BrokenPipeError nach Deutsch Zeit, warte..."); break
                    else: print("FEHLER bei deutscher Zeitansage (gTTS), �berspringe.");

                    # Kurze Pause zwischen Zeit und Wetter
                    try: fifo_file.write(inter_announcement_silence_wave.tobytes()); fifo_file.flush()
                    except BrokenPipeError: print("BrokenPipeError nach Pause Zeit/Wetter, warte..."); break

                    # 2. Deutsche Ansage (Wetter)
                    print(f"Generiere Deutsch Wetter: {german_weather_text}")
                    tts_success = generate_gtts_audio_file(german_weather_text, tts_audio_file_path, 'de', slow_speed=False)
                    if tts_success:
                        tts_audio_data = convert_audio_to_float32_mono_10khz(tts_audio_file_path, SAMPLE_RATE, volume_db=VOLUME_GAIN_DE_DB)
                        if tts_audio_data is None: print("FEHLER bei deutscher Wetteransage (FFmpeg), �berspringe.");
                        else:
                            try: fifo_file.write(tts_audio_data.tobytes()); fifo_file.flush()
                            except BrokenPipeError: print("BrokenPipeError nach Deutsch Wetter, warte..."); break
                    else: print("FEHLER bei deutscher Wetteransage (gTTS), �berspringe.");


                    # 3. Kurze Pause zwischen Sprachen
                    try: fifo_file.write(inter_lang_silence_wave.tobytes()); fifo_file.flush()
                    except BrokenPipeError: print("BrokenPipeError nach Pause (DE-EN), warte..."); break


                    # 4. Englische Ansage (Zeit)
                    # NEU: Angepasste Formatierung f�r nat�rlichere Aussprache
                    english_time_text = now.strftime("It is %I %M and %S seconds %p.") 
                    print(f"Generiere Englisch Zeit: {english_time_text}")
                    tts_success = generate_gtts_audio_file(english_time_text, tts_audio_file_path, 'en', slow_speed=False)
                    if tts_success:
                        tts_audio_data = convert_audio_to_float32_mono_10khz(tts_audio_file_path, SAMPLE_RATE, volume_db=VOLUME_GAIN_EN_DB)
                        if tts_audio_data is None: print("FEHLER bei englischer Zeitansage (FFmpeg), �berspringe.");
                        else:
                            try: fifo_file.write(tts_audio_data.tobytes()); fifo_file.flush()
                            except BrokenPipeError: print("BrokenPipeError nach Englisch Zeit, warte..."); break
                    else: print("FEHLER bei englischer Zeitansage (gTTS), �berspringe.");

                    # Kurze Pause zwischen Zeit und Wetter
                    try: fifo_file.write(inter_announcement_silence_wave.tobytes()); fifo_file.flush()
                    except BrokenPipeError: print("BrokenPipeError nach Pause Zeit/Wetter, warte..."); break

                    # 5. Englische Ansage (Wetter)
                    print(f"Generiere Englisch Wetter: {english_weather_text}")
                    tts_success = generate_gtts_audio_file(english_weather_text, tts_audio_file_path, 'en', slow_speed=False)
                    if tts_success:
                        tts_audio_data = convert_audio_to_float32_mono_10khz(tts_audio_file_path, SAMPLE_RATE, volume_db=VOLUME_GAIN_EN_DB)
                        if tts_audio_data is None: print("FEHLER bei englischer Wetteransage (FFmpeg), �berspringe.");
                        else:
                            try: fifo_file.write(tts_audio_data.tobytes()); fifo_file.flush()
                            except BrokenPipeError: print("BrokenPipeError nach Englisch Wetter, warte..."); break
                    else: print("FEHLER bei englischer Wetteransage (gTTS), �berspringe.");


                    # 6. Kurze Pause zwischen Sprachen (f�r den �bergang zu Franz�sisch, auch wenn auskommentiert)
                    try: fifo_file.write(inter_lang_silence_wave.tobytes()); fifo_file.flush()
                    except BrokenPipeError: print("BrokenPipeError nach Pause (EN-FR), warte..."); break

                    # Kommentar: Franz�sische Ansage ist auskommentiert, wie vom Benutzer gew�nscht.
                    # # 7. Franz�sische Ansage (Zeit)
                    # french_time_text = now.strftime("Il est %H heures %M minutes et %S secondes.") 
                    # print(f"Generiere Franz�sisch Zeit: {french_time_text}")
                    # tts_success = generate_gtts_audio_file(french_time_text, tts_audio_file_path, 'fr', slow_speed=True) # Franz�sisch ggf. langsamer
                    # if tts_success:
                    #     tts_audio_data = convert_audio_to_float32_mono_10khz(tts_audio_file_path, SAMPLE_RATE, volume_db=VOLUME_GAIN_FR_DB)
                    #     if tts_audio_data is None: print("FEHLER bei franz�sischer Zeitansage (FFmpeg), �berspringe.");
                    #     else:
                    #         try: fifo_file.write(tts_audio_data.tobytes()); fifo_file.flush()
                    #         except BrokenPipeError: print("BrokenPipeError nach Franz�sisch Zeit, warte..."); break
                    # else: print("FEHLER bei franz�sischer Zeitansage (gTTS), �berspringe.");
                    

                    # --- Zyklusende ---
                    next_update_time = time.monotonic() + UPDATE_INTERVAL_SECONDS
                    print(f"Alle Ansagen gesendet. N�chster Zyklus beginnt in {UPDATE_INTERVAL_SECONDS} Sekunden (total).")

        except Exception as e:
            print(f"FEHLER im Haupt-Loop: {e}. Versuche es erneut in 5 Sekunden.")
            time.sleep(5)

if __name__ == "__main__":
    main()
