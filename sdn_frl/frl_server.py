import flwr as fl
import logging

# Set logging level
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("FRL_SERVER")

def main():
    # Define strategy (FedAvg is standard)
    strategy = fl.server.strategy.FedAvg(
        fraction_fit=1.0, # Sample 100% of available clients for training
        fraction_evaluate=0.5, # Sample 50% for evaluation
        min_fit_clients=2, # Minimum number of clients to fit
        min_evaluate_clients=1,
        min_available_clients=2,
    )

    # Start Flower server
    logger.info("Starting Federated Reinforcement Learning Server (Flower)...")
    fl.server.start_server(
        server_address="0.0.0.0:8080",
        config=fl.server.ServerConfig(num_rounds=5),
        strategy=strategy,
    )

if __name__ == "__main__":
    main()
