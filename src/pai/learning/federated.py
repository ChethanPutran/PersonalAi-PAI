import torch
import torch.nn as nn
import copy

class FederatedAveraging:
    def __init__(self):
        self.global_model = nn.Linear(10, 2)  # example
        self.client_updates = []
    
    def aggregate(self, client_models):
        avg_state = {}
        for key in self.global_model.state_dict():
            avg_state[key] = torch.stack([m.state_dict()[key].float() for m in client_models]).mean(0)
        self.global_model.load_state_dict(avg_state)
        return self.global_model