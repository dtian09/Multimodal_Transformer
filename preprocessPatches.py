import torch
import numpy as np
from PIL import Image
import os
import json
import sentencepiece as spm
import torchvision.transforms as transforms
from torch.utils.data import Dataset, DataLoader, random_split
from datasets import load_dataset
'''
create dataloaders for image patches
'''

def load_flickr30k():
    """Load the entire Flickr30k dataset and print its structure"""
    print("Loading Flickr30k dataset...")
    dataset = load_dataset("nlphuji/flickr30k")
    
    # Print dataset structure
    print("\nDataset structure:")
    print(f"Available splits: {list(dataset.keys())}")
    for split in dataset.keys():
        print(f"\n{split} split:")
        print(f"Number of examples: {len(dataset[split])}")
        print(f"Features: {dataset[split].features}")
        print(f"First example keys: {list(dataset[split][0].keys())}")
    
    return dataset

class Flickr30kDataset(Dataset):
    def __init__(self, data, patch_size=16, max_length=50, vocab_size=32000):
        self.data = data
        self.patch_size = patch_size
        self.max_length = max_length
        
        # Initialize SentencePiece tokenizer
        self.sp = spm.SentencePieceProcessor()
        self.sp.load('flickr30k.model')
        
        # Image preprocessing (only normalization, no resizing)
        self.transform = transforms.Compose([
            transforms.Resize((256, 256)),  # or any fixed size divisible by patch_size
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], 
                              std=[0.229, 0.224, 0.225])
        ])
        
    def split_image_into_patches(self, image):
        """Split image into patches and flatten them"""
        # image shape: [C, H, W]
        C, H, W = image.shape
        
        # Calculate number of patches in height and width
        H_patches = H // self.patch_size
        W_patches = W // self.patch_size
        
        # Ensure image dimensions are divisible by patch size
        if H % self.patch_size != 0 or W % self.patch_size != 0:
            # Crop image to make it divisible by patch size
            H = H_patches * self.patch_size
            W = W_patches * self.patch_size
            image = image[:, :H, :W]
        
        # Split into patches
        patches = image.unfold(1, self.patch_size, self.patch_size).unfold(2, self.patch_size, self.patch_size)
        patches = patches.contiguous().view(-1, C, self.patch_size, self.patch_size)
        
        return patches
    
    def process_caption(self, caption):
        """Process caption using SentencePiece tokenizer"""
        # Tokenize caption
        tokens = self.sp.encode_as_ids(caption)
        
        # Truncate or pad to max_length
        if len(tokens) > self.max_length - 2:  # -2 for <sos> and </sos>
            tokens = tokens[:self.max_length - 2]
        else:
            tokens = tokens + [self.sp.pad_id()] * (self.max_length - 2 - len(tokens))
        
        # Add <sos> and </sos> tokens
        caption_input = [self.sp.bos_id()] + tokens[:-1]  # caption input
        caption_label = tokens[1:] + [self.sp.eos_id()]  # caption label
        
        return torch.tensor(caption_input), torch.tensor(caption_label)
    
    def __len__(self):
        return len(self.data)
    
    def __getitem__(self, idx):
        # Get image and captions
        item = self.data[idx]
        image = item['image'].convert('RGB')
        captions = item['caption']
        
        # Process image (no resizing)
        image = self.transform(image)
        patches = self.split_image_into_patches(image)
        
        # Process all captions for this image
        processed_captions = []
        for caption in captions:
            caption_input, caption_label = self.process_caption(caption)
            processed_captions.append((caption_input, caption_label))
        
        return {
            'image_patches': patches,
            'captions': processed_captions
        }

def train_sentencepiece_model(dataset, vocab_size=32000):
    """Train a SentencePiece model on the dataset captions"""
    # Extract all captions
    all_captions = []
    print("\nProcessing captions for tokenizer training...")
    for item in dataset:
        all_captions.extend(item['caption'])
    
    print(f"Total number of captions: {len(all_captions)}")
    
    # Write captions to a temporary file
    with open('captions.txt', 'w', encoding='utf-8') as f:
        for caption in all_captions:
            f.write(caption + '\n')
    
    # Train SentencePiece model
    print("\nTraining SentencePiece model...")
    spm.SentencePieceTrainer.train(
        input='captions.txt',
        model_prefix='flickr30k',
        vocab_size=vocab_size,
        model_type='bpe',
        character_coverage=1.0,
        pad_id=0,
        unk_id=1,
        bos_id=2,
        eos_id=3,
        pad_piece='<pad>',
        unk_piece='<unk>',
        bos_piece='<sos>',
        eos_piece='</sos>'
    )

def create_dataloaders(batch_size=32):
    """Create train and test dataloaders with 70-30 split and save metadata."""
    dataset = load_flickr30k()

    all_data = []
    for split in dataset.keys():
        all_data.extend(dataset[split])
    print(f"\nTotal number of examples: {len(all_data)}")

    if not os.path.exists('flickr30k.model'):
        train_sentencepiece_model(all_data)

    flickr_dataset = Flickr30kDataset(all_data)
    total_size = len(flickr_dataset)
    train_size = int(0.7 * total_size)
    test_size = total_size - train_size

    print(f"\nSplitting dataset:")
    print(f"Training set size: {train_size}")
    print(f"Test set size: {test_size}")

    indices = torch.randperm(total_size).tolist()
    train_indices = indices[:train_size]
    test_indices = indices[train_size:]

    # Save indices and batch_size metadata
    torch.save({
        'train_indices': train_indices,
        'test_indices': test_indices,
        'batch_size': batch_size
    }, 'flickr30k_metadata.pt')

    # Use Subset to construct datasets
    train_dataset = torch.utils.data.Subset(flickr_dataset, train_indices)
    test_dataset = torch.utils.data.Subset(flickr_dataset, test_indices)

    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=batch_size)

    return train_loader, test_loader

def load_dataloaders():
    """Load dataloaders using saved metadata."""
    print("Loading dataloader metadata...")
    if not os.path.exists('flickr30k_metadata.pt'):
        print("Metadata file not found. Recreating dataloaders...")
        return create_dataloaders()

    metadata = torch.load('flickr30k_metadata.pt')
    train_indices = metadata['train_indices']
    test_indices = metadata['test_indices']
    batch_size = metadata['batch_size']

    dataset = load_flickr30k()
    all_data = []
    for split in dataset.keys():
        all_data.extend(dataset[split])

    if not os.path.exists('flickr30k.model'):
        train_sentencepiece_model(all_data)

    flickr_dataset = Flickr30kDataset(all_data)

    train_dataset = torch.utils.data.Subset(flickr_dataset, train_indices)
    test_dataset = torch.utils.data.Subset(flickr_dataset, test_indices)

    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=batch_size)

    return train_loader, test_loader

if __name__ == "__main__":
    train_loader, test_loader = create_dataloaders(batch_size=2)

    batch = next(iter(train_loader))
    print(f"Image patches shape: {batch['image_patches'].shape}")
    print(f"Number of captions: {len(batch['captions'][0])}")

    print("\nTesting loading saved dataloaders...")
    train_loader2, test_loader2 = load_dataloaders()
    batch2 = next(iter(train_loader2))
    print(f"Loaded image patches shape: {batch2['image_patches'].shape}")
