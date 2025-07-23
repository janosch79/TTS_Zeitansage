import os
import time
import datetime
import pyttsx3
import numpy as np
import stat
import subprocess

# --- Konfiguration ---
FIFO_PATH = os.path.join(os.path.expanduser("~"), "zeitansage_audio_fifo")
SAMPLE_RATE = 20000  # Abtastrate in Hz (20 kHz)
BEEP_FREQUENCY = 500 # Frequenz des Beep-Tons in Hz
BEEP_DURATION_SECONDS = 0.2 # Dauer eines einzelnen Beep-Tons

# Zeitansage-Zyklus:
UPDATE_INTERVAL_SECONDS = 15 # Zeitintervall zwischen dem Beginn der Ansagen (erhöht für 3 Sprachen)
COUNTDOWN_BEATS = 5 # Anzahl der Beeps vor der Hauptansage
BEEP_CYCLE_INTERVAL = 1.0 # Zeit zwischen den Beep-Beginnen (1 Sekunde für "jede Sekunde")
INTER_LANGUAGE_SILENCE_SECONDS = 0.75 # Pause zwischen den verschiedenen Sprachansagen

SPEAKER_VOLUME = 0.8 # Lautstärke der Sprachausgabe (0.0 - 1.0)
BEEP_VOLUME = 0.5 # Lautstärke des Beep-Tons (0.0 - 1.0)

# NEU: Sprechgeschwindigkeiten pro Sprache (Wörter pro Minute)
SPEAKER_RATE_DE = 180 # Standard für Deutsch
SPEAKER_RATE_EN = 150 # Langsamer für Englisch
SPEAKER_RATE_FR = 150 # Langsamer für Französisch

# --- Hilfsfunktionen ---

def generate_beep_wave(freq, duration, sample_rate, volume):
    """Generiert einen Sinuswellen-Beep (Float32)."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    beep_wave = (volume * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return beep_wave

def generate_silence_wave(duration_seconds, sample_rate):
    """Generiert ein Array von Stille (Float32)."""
    return np.zeros(int(duration_seconds * sample_rate), dtype=np.float32)

def get_voice_by_lang_code(engine, lang_code_prefix):
    """Sucht eine passende Stimme basierend auf einem Sprachcode-Präfix."""
    voices = engine.getProperty('voices')
    for voice in voices:
        if lang_code_prefix.lower() in voice.id.lower():
            return voice.id
    return None # Fallback to default

def generate_tts_wav(text, output_path, volume, speech_rate, lang_code_prefix=None):
    """Generiert eine WAV-Datei aus Text mit pyttsx3 und versucht, die Sprache und Rate einzustellen."""
    engine = pyttsx3.init()
    
    if lang_code_prefix:
        voice_id = get_voice_by_lang_code(engine, lang_code_prefix)
        if voice_id:
            engine.setProperty('voice', voice_id)
            print(f"INFO: Stimme für '{lang_code_prefix}' gefunden und eingestellt: {voice_id}")
        else:
            print(f"WARNUNG: Keine Stimme für '{lang_code_prefix}' gefunden. Verwende Standardstimme.")
    
    engine.setProperty('rate', speech_rate) # HIER WIRD DIE SPRACHRATE GESETZT
    engine.setProperty('volume', volume)
    
    engine.save_to_file(text, output_path)
    engine.runAndWait()

def convert_wav_to_float32_mono_20khz(input_wav_path, target_sample_rate):
    """
    Konvertiert eine WAV-Datei zu einem NumPy float32 Array (mono, target_sample_rate)
    mithilfe von FFmpeg und gibt es zurück.
    """
    ffmpeg_command = [
        'ffmpeg',
        '-i', input_wav_path,
        '-f', 'f32le',               # Ausgabeformat: 32-bit float, little-endian
        '-acodec', 'pcm_f32le',      # Audio-Codec: PCM 32-bit float, little-endian
        '-ar', str(target_sample_rate), # Audio-Abtastrate
        '-ac', '1',                  # Audio-Kanäle (mono)
        '-map_metadata', '-1',       # Keine Metadaten kopieren
        '-loglevel', 'error',        # Nur Fehler ausgeben
        '-'                          # Ausgabe an stdout
    ]
    
    try:
        process = subprocess.run(ffmpeg_command, check=True, capture_output=True)
        audio_data = np.frombuffer(process.stdout, dtype=np.float32)
        return audio_data
    except subprocess.CalledProcessError as e:
        print(f"FEHLER: FFmpeg-Prozess fehlgeschlagen bei Konvertierung von '{input_wav_path}'.")
        print(f"FFmpeg stdout: {e.stdout.decode()}")
        print(f"FFmpeg stderr: {e.stderr.decode()}")
        return None
    except Exception as e:
        print(f"Ein unerwarteter Fehler ist aufgetreten während der FFmpeg-Konvertierung: {e}")
        return None

# --- Haupt-Streaming-Funktion ---

def main():
    try:
        subprocess.run(['ffmpeg', '-version'], check=True, capture_output=True)
        print("FFmpeg gefunden. Wird für Audioverarbeitung verwendet.")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("FEHLER: FFmpeg ist nicht installiert oder nicht im PATH gefunden.")
        print("Bitte installieren Sie FFmpeg (z.B. 'sudo apt install ffmpeg' unter Debian/Ubuntu).")
        return

    if os.path.exists(FIFO_PATH):
        if not stat.S_ISFIFO(os.stat(FIFO_PATH).st_mode):
            print(f"FEHLER: '{FIFO_PATH}' existiert, ist aber keine Named Pipe. Bitte löschen oder umbenennen.")
            return
    else:
        try:
            os.mkfifo(FIFO_PATH)
            print(f"Named Pipe (FIFO) '{FIFO_PATH}' erstellt.")
        except OSError as e:
            print(f"FEHLER: Konnte Named Pipe '{FIFO_PATH}' nicht erstellen: {e}")
            print("Stellen Sie sicher, dass Sie die Berechtigung haben, in Ihrem Home-Verzeichnis zu schreiben.")
            return

    # Beep-Wellenform einmalig generieren und im RAM halten
    beep_wave = generate_beep_wave(BEEP_FREQUENCY, BEEP_DURATION_SECONDS, SAMPLE_RATE, BEEP_VOLUME)
    
    # Stille-Segment für die Lücke zwischen den Beeps
    silence_after_beep_duration = BEEP_CYCLE_INTERVAL - BEEP_DURATION_SECONDS
    silence_after_beep_wave = generate_silence_wave(silence_after_beep_duration, SAMPLE_RATE)
    
    # Kombiniertes Beep+Stille Segment, das zyklisch gesendet wird
    pulsed_beep_segment = np.concatenate((beep_wave, silence_after_beep_wave))

    # Stille-Segment zwischen den Sprachen
    inter_lang_silence_wave = generate_silence_wave(INTER_LANGUAGE_SILENCE_SECONDS, SAMPLE_RATE)

    tts_wav_path = os.path.join(os.path.expanduser("~"), "zeitansage_temp_tts.wav")

    print(f"Schreibe Audio-Stream als float32 in Named Pipe '{FIFO_PATH}' mit {SAMPLE_RATE} Hz...")
    print("Starte einen Reader (z.B. VLC oder GNU Radio) aus dieser Pipe.")

    next_update_time = time.monotonic() # Zeitpunkt für den Beginn des nächsten Update-Zyklus

    while True:
        try:
            with open(FIFO_PATH, 'wb') as fifo_file:
                print(f"FIFO '{FIFO_PATH}' geöffnet. Warte auf Reader...")
                
                while True: # Innere Schleife, die läuft, solange der Reader verbunden ist
                    time_until_next_cycle = next_update_time - time.monotonic()
                    if time_until_next_cycle > 0:
                        print(f"Warte {time_until_next_cycle:.2f} Sekunden bis zum nächsten Ansage-Zyklus...")
                        silence_duration = time_until_next_cycle
                        block_size_samples = int(SAMPLE_RATE * 0.1) # 100ms Blöcke
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
                                print(f"BrokenPipeError während Stille: Reader von '{FIFO_PATH}' hat Verbindung getrennt. Warte auf neuen Reader...")
                                break
                            # time.sleep(0.01) # Kurze Pause, um CPU nicht zu überlasten, kann bei stabilen Systemen auch weggelassen werden
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
                            print(f"BrokenPipeError während Countdown: Reader von '{FIFO_PATH}' hat Verbindung getrennt. Warte auf neuen Reader...")
                            break
                    else: # Only runs if loop completed without break
                        i = 0 # Indicate countdown completed

                    if i != 0: # If countdown loop was broken
                        break # Break out of inner while loop as well


                    # --- Zeitansagen in verschiedenen Sprachen ---
                    now = datetime.datetime.now()
                    
                    # 1. Deutsche Ansage
                    german_text = now.strftime("Es ist %H Uhr %M Minuten und %S Sekunden.")
                    print(f"Generiere Deutsch: {german_text}")
                    generate_tts_wav(german_text, tts_wav_path, SPEAKER_VOLUME, SPEAKER_RATE_DE, lang_code_prefix='de')
                    tts_audio_data = convert_wav_to_float32_mono_20khz(tts_wav_path, SAMPLE_RATE)
                    if tts_audio_data is None: print("Fehler bei deutscher Ansage, überspringe.")
                    else:
                        try: fifo_file.write(tts_audio_data.tobytes()); fifo_file.flush()
                        except BrokenPipeError: print("BrokenPipeError nach Deutsch, warte..."); break


                    # 2. Kurze Pause
                    try: fifo_file.write(inter_lang_silence_wave.tobytes()); fifo_file.flush()
                    except BrokenPipeError: print("BrokenPipeError nach Pause, warte..."); break


                    # 3. Englische Ansage
                    english_text = now.strftime("It is %I %M %S %p.") 
                    print(f"Generiere Englisch: {english_text}")
                    # Hier wird die neue Rate für Englisch übergeben
                    generate_tts_wav(english_text, tts_wav_path, SPEAKER_VOLUME, SPEAKER_RATE_EN, lang_code_prefix='en')
                    tts_audio_data = convert_wav_to_float32_mono_20khz(tts_wav_path, SAMPLE_RATE)
                    if tts_audio_data is None: print("Fehler bei englischer Ansage, überspringe.")
                    else:
                        try: fifo_file.write(tts_audio_data.tobytes()); fifo_file.flush()
                        except BrokenPipeError: print("BrokenPipeError nach Englisch, warte..."); break


                    # 4. Kurze Pause
                    try: fifo_file.write(inter_lang_silence_wave.tobytes()); fifo_file.flush()
                    except BrokenPipeError: print("BrokenPipeError nach Pause, warte..."); break


                    # 5. Französische Ansage
                    french_text = now.strftime("Il est %H heures %M minutes et %S secondes.")
                    print(f"Generiere Französisch: {french_text}")
                    # Hier wird die neue Rate für Französisch übergeben
                    generate_tts_wav(french_text, tts_wav_path, SPEAKER_VOLUME, SPEAKER_RATE_FR, lang_code_prefix='fr')
                    tts_audio_data = convert_wav_to_float32_mono_20khz(tts_wav_path, SAMPLE_RATE)
                    if tts_audio_data is None: print("Fehler bei französischer Ansage, überspringe.")
                    else:
                        try: fifo_file.write(tts_audio_data.tobytes()); fifo_file.flush()
                        except BrokenPipeError: print("BrokenPipeError nach Französisch, warte..."); break
                    

                    # --- Zyklusende ---
                    next_update_time = time.monotonic() + UPDATE_INTERVAL_SECONDS
                    print(f"Alle Ansagen gesendet. Nächster Zyklus beginnt in {UPDATE_INTERVAL_SECONDS} Sekunden (total).")

        except Exception as e:
            print(f"Fehler beim Öffnen der FIFO: {e}. Versuche es erneut in 5 Sekunden.")
            time.sleep(5)

if __name__ == "__main__":
    main()
