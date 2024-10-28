import os
import pickle
import numpy as np
import pandas as pd
import torch
import requests
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from langdetect import detect
from newspaper import Article
from torch.utils.data import Dataset, DataLoader
from transformers import BertForSequenceClassification, BertTokenizer, AdamW, Trainer
import streamlit as st
from wordcloud import WordCloud
import matplotlib.pyplot as plt
from lime.lime_text import LimeTextExplainer
from tempfile import TemporaryDirectory
import shutil
import pytesseract
from PIL import Image
import speech_recognition as sr
from pydub import AudioSegment
import tempfile
from pydub.utils import which
from cachetools import TTLCache
from concurrent.futures import ThreadPoolExecutor
import time
from requests.exceptions import ConnectTimeout, ConnectionError
from dateutil import tz

# File paths
RF_FEEDBACK_FILE = 'rf_feedback_log.csv'
FINBERT_FEEDBACK_FILE = 'finbert_feedback_log.csv'
MODEL_SAVE_PATH = './saved_best_model/random_forest_model.pkl'
FINBERT_MODEL_SAVE_PATH = './saved_finbert_model'

# Set path for the ffmpeg converter
AudioSegment.converter = which('ffmpeg')

# Ensure feedback log files exist
for file in [RF_FEEDBACK_FILE, FINBERT_FEEDBACK_FILE]:
    if not os.path.exists(file):
        pd.DataFrame(columns=["text", "prediction", "correct"]).to_csv(file, index=False)

# Load FinBERT model and tokenizer
finbert = BertForSequenceClassification.from_pretrained(
    FINBERT_MODEL_SAVE_PATH,
    num_labels=2,  # Ensure number of labels is set correctly
    ignore_mismatched_sizes=True  # Ignore mismatched sizes to resolve loading errors
)
tokenizer = BertTokenizer.from_pretrained(FINBERT_MODEL_SAVE_PATH)

MAX_SEQ_LENGTH = 256  # Define a maximum sequence length to prevent out-of-range errors

# Dataset class for FinBERT fine-tuning
class FeedbackDataset(Dataset):
    def __init__(self, texts, labels, tokenizer):
        self.texts = texts
        self.labels = labels
        self.tokenizer = tokenizer

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, idx):
        tokens = self.tokenizer(self.texts[idx], padding='max_length', truncation=True, max_length=MAX_SEQ_LENGTH, return_tensors="pt")
        input_ids = tokens['input_ids'].squeeze()
        attention_mask = tokens['attention_mask'].squeeze()
        return {
            'input_ids': input_ids,
            'attention_mask': attention_mask,
            'labels': torch.tensor(self.labels[idx], dtype=torch.long)
        }

# Custom collate function to handle varying tensor sizes
def collate_fn(batch):
    input_ids = torch.nn.utils.rnn.pad_sequence([item['input_ids'] for item in batch], batch_first=True, padding_value=0)
    attention_mask = torch.nn.utils.rnn.pad_sequence([item['attention_mask'] for item in batch], batch_first=True, padding_value=0)
    labels = torch.tensor([item['labels'] for item in batch], dtype=torch.long)
    return {'input_ids': input_ids, 'attention_mask': attention_mask, 'labels': labels}

# Load RandomForest model and metadata
def load_model_and_metadata():
    with open(MODEL_SAVE_PATH, 'rb') as f:
        model = pickle.load(f)
    with open('./saved_best_model/metadata.pkl', 'rb') as f:
        metadata = pickle.load(f)
    return model, metadata

random_forest_model, metadata = load_model_and_metadata()
scaler = metadata['scaler']
vectorizer = metadata['vectorizer']

# Function to get FinBERT predictions
def get_finbert_predictions(text):
    inputs = tokenizer([text], padding=True, truncation=True, max_length=MAX_SEQ_LENGTH, return_tensors="pt")
    with torch.no_grad():
        outputs = finbert(**inputs)
        logits = outputs.logits
        probabilities = torch.nn.functional.softmax(logits, dim=1).numpy()
        prediction = np.argmax(probabilities, axis=1)[0]
    return prediction, probabilities.max()

# Function to scrape metadata from a URL
def prepare_metadata_features(url):
    retries = 3
    for attempt in range(retries):
        try:
            article = Article(url, memoize_articles=False, request_timeout=15)
            article.download()
            article.parse()
            title = article.title
            authors = article.authors
            published_date = article.publish_date
            if published_date is not None:
                published_date = pd.Timestamp(published_date).tz_localize(None)  # Remove timezone information if present
            text = article.text[:1000]  # Limit to 1000 characters
            has_image = 1 if article.top_image else 0
            language = detect(text) if text else 'unknown'
            days_since_published = (pd.Timestamp.now() - pd.Timestamp(published_date)).days if published_date else 0
            
            # Prepare metadata for scaling
            metadata_dict = {
                'title_length': [len(title)],
                'num_authors': [len(authors)],
                'has_image': [has_image],
                'days_since_published': [days_since_published],
                'language_en': [1 if language == 'en' else 0]
            }
            metadata_df = pd.DataFrame(metadata_dict)
            scaled_features = scaler.transform(metadata_df)
            return scaled_features, text
        except (ConnectTimeout, ConnectionError) as e:
            st.warning(f"Attempt {attempt + 1} failed due to connection error. Retrying...")
            time.sleep(2)  # Sleep before retrying
        except Exception as e:
            st.error(f"Error while preparing metadata: {e}")
            return None, None

    # After retrying, if it still fails, show an error
    st.error(f"Failed to download article from URL: {url} after {retries} attempts.")
    return None, None

# Function to predict with RandomForest
def get_random_forest_predictions(text, metadata_features):
    tfidf_features = vectorizer.transform([text]).toarray()
    combined_features = np.hstack([metadata_features, tfidf_features])
    rf_pred = random_forest_model.predict(combined_features)[0]
    rf_conf = random_forest_model.predict_proba(combined_features).max()
    return rf_pred, rf_conf

# Ensemble prediction using both FinBERT and RandomForest
def ensemble_prediction(text=None, url=None):
    if url:
        metadata_features, article_text = prepare_metadata_features(url)
        if metadata_features is None:
            st.warning("Not enough metadata to make a reliable prediction.")
            return "Unable to determine", 0.0
        
        rf_pred, rf_conf = get_random_forest_predictions(article_text, metadata_features)
        return "Real" if rf_pred == 1 else "Fake", rf_conf
    elif text:
        finbert_pred, finbert_conf = get_finbert_predictions(text)
        dummy_metadata = np.zeros((1, 5))  # Placeholder for metadata
        rf_pred, rf_conf = get_random_forest_predictions(text, dummy_metadata)

        # Weighted combination of confidences, giving more weight to Random Forest
        final_conf = (0.3 * finbert_conf + 0.7 * rf_conf)
        final_pred = 1 if (0.3 * finbert_pred + 0.7 * rf_pred) >= 0.5 else 0
        return "Real" if final_pred == 1 else "Fake", final_conf

    return "Unable to determine", 0.0

# Log feedback to CSV
def log_feedback_rf(text, original_prediction, correct_label):
    feedback = pd.DataFrame([[text, original_prediction, correct_label]], columns=["text", "prediction", "correct"])
    try:
        with open(RF_FEEDBACK_FILE, mode='a+', newline='', encoding='utf-8') as f:
            feedback.to_csv(f, header=False, index=False)
    except Exception as e:
        print(f"Error logging feedback for Random Forest: {e}")

def log_feedback_finbert(text, original_prediction, correct_label):
    feedback = pd.DataFrame([[text, original_prediction, correct_label]], columns=["text", "prediction", "correct"])
    try:
        with open(FINBERT_FEEDBACK_FILE, mode='a+', newline='', encoding='utf-8') as f:
            feedback.to_csv(f, header=False, index=False)
    except Exception as e:
        print(f"Error logging feedback for FinBERT: {e}")

# Function to extract text from an image
def extract_text_from_image(image):
    text = pytesseract.image_to_string(image)
    return text

# Function to convert audio file to WAV
def convert_to_wav(audio_file):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_wav_file:
        sound = AudioSegment.from_file(audio_file)
        sound.export(temp_wav_file.name, format="wav")
        return temp_wav_file.name

# Speech recognition function
def recognize_speech_from_audio(audio_path):
    recognizer = sr.Recognizer()
    with sr.AudioFile(audio_path) as source:
        audio_data = recognizer.record(source)
    return recognizer.recognize_google(audio_data)

# Collecting feedback
def collect_feedback():
    if "last_feedback_data" in st.session_state and st.session_state.last_feedback_data:
        if "feedback_value" not in st.session_state:
            st.session_state.feedback_value = "Select"

        # Check if feedback has already been submitted
        if not st.session_state.feedback_submitted:
            # Ask if the prediction was correct
            st.session_state.feedback_value = st.radio(
                "Was this prediction correct?", options=["Select", "Yes", "No"], index=0
            )

            # If the prediction was incorrect, ask for the correct label
            if st.session_state.feedback_value == "No":
                st.session_state.correct_label = st.radio(
                    "What is the correct label?", options=[("Fake", 0), ("Real", 1)], index=1
                )[1]  # Get only the numeric value (0 or 1)

            # Only show the button if feedback is selected
            if st.session_state.feedback_value != "Select" and st.button("Submit Feedback"):
                submit_feedback()
                
def submit_feedback():
    # Retrieve the data for feedback
    if st.session_state.last_feedback_data:
        text_or_url, prediction = st.session_state.last_feedback_data
        if st.session_state.feedback_value == "Yes":
            correct_label = 1 if prediction == "Real" else 0  # Use prediction if it's correct
        else:
            correct_label = st.session_state.correct_label  # Use the provided correct label (0 or 1)

        # Log feedback for both models with the correct label (0 or 1 only)
        log_feedback_rf(text_or_url, prediction, correct_label)
        log_feedback_finbert(text_or_url, prediction, correct_label)

        # Mark feedback as submitted
        st.session_state.feedback_submitted = True

        # Confirmation message
        st.success("Thank you for your feedback! It has been saved successfully.")
        # Clear the UI related to feedback to avoid duplicate display
        st.session_state.feedback_value = "Select"
    else:
        st.error("No prediction data found to submit feedback.")                

# Function to reload the RandomForest model
def reload_rf_model():
    global random_forest_model
    random_forest_model, _ = load_model_and_metadata()
    st.success("Random Forest model reloaded!")


# Display feedback count
def get_feedback_count_rf():
    rf_feedback_data = pd.read_csv(RF_FEEDBACK_FILE)
    return len(rf_feedback_data)

def get_feedback_count_finbert():
    finbert_feedback_data = pd.read_csv(FINBERT_FEEDBACK_FILE)
    return  len(finbert_feedback_data)

# Retrain models based on feedback
def retrain_random_forest():
    feedback_data = pd.read_csv(RF_FEEDBACK_FILE)
    if len(feedback_data) < 500:
        return  # Not enough feedback to retrain

    # Retrain RandomForest
    X_texts = feedback_data["text"].str.strip().str.replace('"', '')
    y_labels = feedback_data["correct"]
    X_features = vectorizer.transform(X_texts).toarray()
    dummy_metadata = np.zeros((X_features.shape[0], 5))
    combined_features = np.hstack([dummy_metadata, X_features])
    random_forest_model.fit(combined_features, y_labels)
    with open(MODEL_SAVE_PATH, 'wb') as f:
        pickle.dump(random_forest_model, f)
    st.success("Random Forest retrained with feedback data!")

    # Clear feedback after retraining
    os.remove(RF_FEEDBACK_FILE)
    reload_rf_model()
    
def save_finbert_model(finbert, tokenizer, save_path):
    os.makedirs(save_path, exist_ok=True)

    # Adjust the classifier to ensure it has the correct size (2 labels)
    finbert.classifier = torch.nn.Linear(finbert.config.hidden_size, 2)

    # Save model state_dict and tokenizer
    finbert.save_pretrained(save_path, safe_serialization=False)
    tokenizer.save_pretrained(save_path)


# Function to reload the FinBERT model
def reload_finbert_model():
    global finbert, tokenizer

    # Load the model with mismatched sizes allowed
    finbert = BertForSequenceClassification.from_pretrained(
        'saved_finbert_model',  # Updated the path to meet naming conventions
        num_labels=2,  # Explicitly set the number of labels
        ignore_mismatched_sizes=True
    )
    tokenizer = BertTokenizer.from_pretrained('saved_finbert_model')

    # Reinitialize classifier to match the required number of labels (2)
    finbert.classifier = torch.nn.Linear(finbert.config.hidden_size, 2)

    st.success("FinBERT model reloaded!")
    
# Retrain FinBERT model
def retrain_finbert():
    feedback_data = pd.read_csv(FINBERT_FEEDBACK_FILE)

    # Ensure there is enough feedback data to retrain
    if len(feedback_data) < 2000:
        return

    st.info("Retraining FinBERT...")

    # Load model and tokenizer from the saved directory
    finbert = BertForSequenceClassification.from_pretrained(
        'saved_finbert_model',  # Updated the path to meet naming conventions
        num_labels=2,
        ignore_mismatched_sizes=True
    )
    tokenizer = BertTokenizer.from_pretrained('saved_finbert_model')

    # Adjust the classifier to match the expected number of labels (2)
    finbert.classifier = torch.nn.Linear(finbert.config.hidden_size, 2)

    # Prepare dataset for retraining
    X_texts = feedback_data["text"].str.strip().str.replace('"', '')
    y_labels = feedback_data["correct"]

    dataset = FeedbackDataset(X_texts.tolist(), y_labels.tolist(), tokenizer)
    dataloader = DataLoader(dataset, batch_size=4, shuffle=True, collate_fn=collate_fn)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    finbert.to(device)

    # Optimizer setup
    optimizer = AdamW(finbert.parameters(), lr=1e-5)

    # Training loop
    finbert.train()
    for epoch in range(2):
        for batch in dataloader:
            input_ids = batch['input_ids'].to(device)
            attention_mask = batch['attention_mask'].to(device)
            labels = batch['labels'].to(device)

            if input_ids.shape[0] == 0 or attention_mask.shape[0] == 0 or labels.shape[0] == 0:
                continue

            outputs = finbert(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
            loss = outputs.loss
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()

    # Clear GPU cache to free up memory
    torch.cuda.empty_cache()

    # Save the retrained model and tokenizer to avoid future mismatches
    # Save the retrained model and tokenizer to avoid future mismatches
    output_model_dir = 'saved_finbert_model'
    if os.path.exists(output_model_dir):
        for root, dirs, files in os.walk(output_model_dir, topdown=False):
            for name in files:
                try:
                    os.remove(os.path.join(root, name))
                except PermissionError:
                    st.warning(f"Permission denied while deleting file: {name}")
            for name in dirs:
                try:
                    os.rmdir(os.path.join(root, name))
                except PermissionError:
                    st.warning(f"Permission denied while deleting directory: {name}")


    # Save retrained model
    save_finbert_model(finbert, tokenizer, output_model_dir)

    st.success("FinBERT retrained and saved successfully!")

    # Reload the model after retraining
    reload_finbert_model()

    # Clear feedback data after retraining
    os.remove(FINBERT_FEEDBACK_FILE)

    
def retrain_if_feedback_threshold():
    feedback_count_rf = len(pd.read_csv(RF_FEEDBACK_FILE))
    feedback_count_finbert = len(pd.read_csv(FINBERT_FEEDBACK_FILE))
    if feedback_count_rf >= 500:
        retrain_random_forest()
    if feedback_count_finbert >= 2000:
        retrain_finbert()      
def main():
    st.title("📊 Financial News Authenticity Predictor")
    
    # Initialize session state variables
    if "feedback_submitted" not in st.session_state:
        st.session_state.feedback_submitted = False
    if "last_feedback_data" not in st.session_state:
        st.session_state.last_feedback_data = None

    # Tabs for different input types
    tab1, tab2, tab3, tab4 = st.tabs(["Text Input", "URL Input", "Audio Input", "Image Input"])

    # Text Input Tab
    with tab1:
        text = st.text_area("Enter the news text:")
        if st.button("Predict", key="text"):
            if text:
                result, confidence = ensemble_prediction(text=text)
                st.write(f"The text is predicted to be: **{result}** (Confidence: {confidence:.2f})")
                # Store the prediction data for feedback
                st.session_state.last_feedback_data = (text, result)
                st.session_state.feedback_submitted = False  # Reset feedback submission state

    # URL Input Tab
    with tab2:
        url = st.text_input("Enter the news URL:")
        if st.button("Predict", key="url"):
            if url:
                with st.spinner('Fetching and analyzing URL...'):
                    result, confidence = ensemble_prediction(url=url)
                    st.write(f"The news is predicted to be: **{result}** (Confidence: {confidence:.2f})")
                # Store the prediction data for feedback
                st.session_state.last_feedback_data = (url, result)
                st.session_state.feedback_submitted = False  # Reset feedback submission state

    # Audio Input Tab
    with tab3:
        st.header("Speech Recognition")
        uploaded_audio = st.file_uploader("Upload an audio file", type=["wav", "mp3", "ogg"])

        if uploaded_audio:
            st.audio(uploaded_audio, format="audio/wav")
            audio_path = convert_to_wav(uploaded_audio)
            try:
                text = recognize_speech_from_audio(audio_path)
                st.write("Extracted Text from uploaded audio:")
                st.write(text)
                result, confidence = ensemble_prediction(text=text)
                st.write(f"Prediction: {result} (Confidence: {confidence:.2f})")
                # Store the prediction data for feedback
                st.session_state.last_feedback_data = (text, result)
                st.session_state.feedback_submitted = False  # Reset feedback submission state
            except sr.UnknownValueError:
                st.write("Could not understand the audio.")
            except sr.RequestError:
                st.write("Could not request results from the service.")

        # Microphone input
        st.write("Or use your microphone to speak:")
        if st.button("Start Listening"):
            recognizer = sr.Recognizer()
            with sr.Microphone() as source:
                st.write("Listening...")
                audio = recognizer.listen(source)
                try:
                    text = recognizer.recognize_google(audio)
                    st.write("Extracted Text from microphone:")
                    st.write(text)
                    result, confidence = ensemble_prediction(text=text)
                    st.write(f"Prediction: {result} (Confidence: {confidence:.2f})")
                    # Store the prediction data for feedback
                    st.session_state.last_feedback_data = (text, result)
                    st.session_state.feedback_submitted = False  # Reset feedback submission state
                except sr.UnknownValueError:
                    st.write("Could not understand the speech.")
                except sr.RequestError as e:
                    st.write(f"Request error: {e}")

    # Image Input Tab
    with tab4:
        st.header("Image Text Extraction")
        uploaded_file = st.file_uploader("Upload an image", type=["png", "jpg", "jpeg"])
        camera_image = st.camera_input("Or take a photo")

        if uploaded_file or camera_image:
            image = Image.open(uploaded_file if uploaded_file else camera_image)
            st.image(image, caption="Selected Image", use_column_width=True)
            extracted_text = extract_text_from_image(image)
            st.write("Extracted Text from Image:")
            st.text(extracted_text)
            result, confidence = ensemble_prediction(text=extracted_text)
            st.write(f"Prediction: {result} (Confidence: {confidence:.2f})")

    # Collect feedback only if prediction has been made
    if "last_feedback_data" in st.session_state and not st.session_state.feedback_submitted:
        collect_feedback()

    retrain_if_feedback_threshold()
    
    # st.sidebar.title("FAQ")
    # st.sidebar.markdown("**Q: How does this model work?**")
    # st.sidebar.markdown("A: This model uses FinBERT for text analysis and RandomForest for metadata analysis. Predictions are made using an ensemble approach.")
    # st.sidebar.markdown("**Q: What is the confidence score?**")
    # st.sidebar.markdown("A: The confidence score shows how sure the model is about its prediction.")
    # st.sidebar.markdown("**Q: Can I trust these predictions?**")
    # st.sidebar.markdown("A: The predictions are intended as guidance, not absolute truth.")
    # st.sidebar.markdown("**Q: What's the use of feedback?**")
    # st.sidebar.markdown("A: After receiving a prediction, you can indicate if the prediction was correct by selecting 'Yes' or 'No' and clicking 'Submit Feedback'. Your feedback helps improve the model.")



if __name__ == "__main__":
    # Ensure GPU is available
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    finbert.to(device)
    main()