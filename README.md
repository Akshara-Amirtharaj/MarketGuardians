
# Financial News Authenticity Predictor

## Overview
The **Financial News Authenticity Predictor** aims to tackle misinformation in financial reporting by leveraging a **hybrid AI model**. It combines **FinBERT**, a transformer-based model for financial text analysis, with a **Random Forest Classifier** that incorporates metadata from the **GDELT database**. The platform is deployed using **Streamlit**, providing a user-friendly web interface for real-time predictions.

## Key Features
- **Hybrid Model**: FinBERT + Random Forest for improved prediction accuracy.
- **Multiple Input Modes**: Supports text, URLs, images, and audio inputs.
- **Real-Time Feedback**: Instant prediction results with confidence scores.
- **Feedback Loop**: User corrections stored for periodic model retraining.
- **Scalable & Open-Source**: Available on GitHub with future extensions planned.

## Installation & Usage
1. **Clone the Repository**:
   ```bash
   git clone https://github.com/Akshara-Amirtharaj/MarketGuardians.git
   cd MarketGuardians
   ```

2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the Application**:
   ```bash
   streamlit run app.py
   ```

4. **Provide Input**:  
   - Submit news through text, URLs, images, or audio files.
   - Get real-time predictions on authenticity and confidence scores.

## Technologies Used
- **FinBERT**: Financial text classification.
- **Random Forest**: Metadata-based predictions.
- **Streamlit**: Web-based interface.
- **GDELT Database**: News articles and metadata.
- **Newspaper3k**: Web scraping.
- **Tesseract OCR**: Extracts text from images.
- **Google SpeechRecognition API**: Converts audio to text.

## Achieved Results
- **FinBERT**: 99% accuracy in financial text classification.
- **Random Forest**: 98% accuracy handling metadata.
- **Ensemble Model**: High precision and recall with F1-scores above 0.98.

## Contributors
- **Akshara V**  
- **Hariesh R**  
- **Lithikha B**

## Future Enhancements
- **Scheduled Model Retraining**: Time-based updates for better accuracy.
- **API Integration**: Seamless embedding into financial apps.
- **Cross-Domain Expansion**: Extendable to healthcare and political domains.

## License
This project is open-source under the [MIT License](LICENSE).
